from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request


class TrustedProxiesMiddleware(BaseHTTPMiddleware):
    """
    Middleware to correctly parse the client IP based on a trusted number of proxies.

    This is safer than trusting all headers ("*") and more flexible than hardcoding IPs
    for cloud environments (Render, AWS, Heroku) where LB IPs are dynamic.
    """

    def __init__(self, app, proxies_count: int = 1) -> None:
        super().__init__(app)
        self.proxies_count = proxies_count

    async def dispatch(self, request: Request, call_next):
        if self.proxies_count > 0:
            x_forwarded_for = request.headers.get("x-forwarded-for")

            if x_forwarded_for:
                # The header is a comma-separated list: "client, proxy1, proxy2"
                ips = [ip.strip() for ip in x_forwarded_for.split(",")]

                # If we have enough IPs in the chain, trust the Nth one from the end.
                # Render/AWS LB guarantees appending to the end.
                if len(ips) >= self.proxies_count:
                    real_ip = ips[-self.proxies_count]

                    # Update the scope so that request.client.host returns this IP.
                    # This ensures 'slowapi' and logging see the correct address.
                    port = request.scope["client"][1] if request.scope.get("client") else 0
                    request.scope["client"] = (real_ip, port)

        return await call_next(request)
