from django.http import HttpResponse, JsonResponse


def index(request):
    return HttpResponse("Hello, World!")


def health(request):
    return JsonResponse({"status": "ok"})
