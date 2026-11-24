from django.urls import path

from . import views

urlpatterns = [
    path('search/', views.search, name='wiki_api_search'),
    path('topics/', views.topics, name='wiki_api_topics'),
    path('topic-pages/', views.topic_pages, name='wiki_api_topic_pages'),
    path('recent/', views.recent, name='wiki_api_recent'),
    path('pages/<str:slug>/', views.page, name='wiki_api_page'),
]
