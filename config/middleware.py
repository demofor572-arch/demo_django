import logging
from django.http import JsonResponse


class JsonExceptionMiddleware:
    """Catch unhandled exceptions, log traceback and return a JSON 500.

    This middleware helps in production by ensuring the process logs the
    exception (visible in hosting logs) while returning a simple JSON
    response so frontends don't attempt to parse HTML error pages.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        try:
            return self.get_response(request)
        except Exception:
            logging.exception("Unhandled exception during request")
            return JsonResponse({"error": "Internal server error"}, status=500)
