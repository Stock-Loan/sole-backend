from starlette.types import ASGIApp, Receive, Scope, Send


class TrustedProxiesMiddleware:
    """
    Middleware to correctly parse the client IP based on a trusted number of proxies.

    This is safer than trusting all headers ("*") and more flexible than hardcoding IPs
    for cloud environments (Render, AWS, Heroku) where LB IPs are dynamic.
    """

    def __init__(self, app: ASGIApp, proxies_count: int = 1) -> None:
        self.app = app
        self.proxies_count = proxies_count

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and self.proxies_count > 0:
            headers = dict(scope.get("headers", []))
            x_forwarded_for = headers.get(b"x-forwarded-for", b"").decode()

            if x_forwarded_for:
                # The header is a comma-separated list: "client, proxy1, proxy2"
                ips = [ip.strip() for ip in x_forwarded_for.split(",")]

                # X-Forwarded-For is: "client, proxy1, proxy2, ...".
                # If we trust N proxies at the end, the client is at index -(N+1).
                if len(ips) > self.proxies_count:
                    real_ip = ips[-(self.proxies_count + 1)]

                    # Update the scope so that request.client.host returns this IP.
                    # This ensures 'slowapi' and logging see the correct address.
                    port = scope["client"][1] if scope.get("client") else 0
                    scope["client"] = (real_ip, port)

        await self.app(scope, receive, send)
