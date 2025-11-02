from django.urls import path
from . import views

urlpatterns = [
    # List all tags
    path('', views.tag_list, name='tag_list'),
    # Pages for a specific tag
    path('<slug:tag_slug>/', views.tag_pages, name='tag_pages'),
]
