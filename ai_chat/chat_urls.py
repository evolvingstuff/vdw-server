"""URLs of the chat subdomain (see ChatHostMiddleware)."""

from django.urls import path

from . import views

urlpatterns = [
    path('', views.app, name='chat_app'),
    path('config.json', views.config, name='chat_config'),
    path('tool', views.tool, name='chat_tool'),
]
