"""A stand-in for a provider API: canned JSON per path, and a log of every request made."""

import httpx


class FakeApi:
    def __init__(self, routes):
        # {url path: JSON body | httpx.Response | callable(request) -> either}
        self.routes = routes
        self.requests = []

    def __call__(self, request):
        self.requests.append(request)
        handler = self.routes.get(request.url.path)
        if handler is None:
            return httpx.Response(404, json={"message": f"no route for {request.url.path}"})
        if callable(handler):
            handler = handler(request)
        if isinstance(handler, httpx.Response):
            return handler
        return httpx.Response(200, json=handler)

    @property
    def transport(self):
        return httpx.MockTransport(self)

    @property
    def methods(self):
        return {request.method for request in self.requests}

    def params(self, path):
        return [dict(r.url.params) for r in self.requests if r.url.path == path]


def cloudflare_page(result, page=1, total_pages=1):
    return {
        "success": True,
        "errors": [],
        "messages": [],
        "result": result,
        "result_info": {"page": page, "per_page": 50, "total_pages": total_pages},
    }
