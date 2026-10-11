from django.urls import path
from . import views

urlpatterns = [
    # Topic browser
    path('', views.tag_list, name='tag_list'),
    # Page counts for combinations of topics (used by the topic browser)
    path('api/counts/', views.tag_counts_api, name='tag_counts_api'),
    # Pages for a topic, optionally combined with more topics via ?and=<slug>
    path('<slug:tag_slug>/', views.tag_pages, name='tag_pages'),
]
