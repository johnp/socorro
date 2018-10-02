from django.shortcuts import render


class SetRemoteAddrFromRealIP(object):
    """
    Middleware that sets REMOTE_ADDR based on HTTP_X_REAL_IP, if the
    latter is set. This is useful if you're sitting behind a reverse proxy that
    causes each request's REMOTE_ADDR to be set to 127.0.0.1.

    Note that this does NOT validate HTTP_X_REAL_IP. If you're not behind
    a reverse proxy that sets HTTP_X_REAL_IP automatically, do not use
    this middleware. Anybody can spoof the value of HTTP_X_REAL_IP, and
    because this sets REMOTE_ADDR based on HTTP_X_REAL_IP, that means
    anybody can "fake" their IP address. Only use this when you can absolutely
    trust the value of HTTP_X_REAL_IP.

    """
    def __init__(self, get_response=None):
        self.get_response = get_response

    def __call__(self, request):
        self.process_request(request)
        return self.get_response(request)

    def process_request(self, request):
        real_ip = request.META.get('HTTP_X_REAL_IP')
        if real_ip:
            request.META['REMOTE_ADDR'] = real_ip


class Pretty400Errors(object):
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)

        if (
            response.status_code == 400 and
            not request.is_ajax() and
            response['Content-Type'].startswith('text/html')
        ):
            return render(
                request,
                '400.html',
                {'error': response.content},
                status=400
            )
        return response
