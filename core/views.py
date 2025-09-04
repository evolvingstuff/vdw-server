from django.shortcuts import render


def homepage(request):
    """Homepage view"""
    return render(request, 'core/homepage.html')
