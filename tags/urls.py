from django.urls import path
from . import views

urlpatterns = [
    path('<slug:tag_slug>/', views.tag_posts, name='tag_posts'),
]