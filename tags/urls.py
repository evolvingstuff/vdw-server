from django.urls import path
from . import views

urlpatterns = [
    path('<slug:tag_slug>/', views.tag_pages, name='tag_pages'),
]
