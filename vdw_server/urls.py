"""
URL configuration for vdw_server project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.2/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from django.views.generic import RedirectView
from pages.views import preview_markdown, page_list, page_detail, upload_media
from site_pages.views import homepage, site_page_detail
from search.views import search_api
from vdw_server.admin_views import manual_backup, manual_restore

urlpatterns = [
    path('admin/preview-markdown/', preview_markdown, name='preview_markdown'),
    path('admin/upload-media/', upload_media, name='upload_media'),
    path('admin/manual-backup/', admin.site.admin_view(manual_backup), name='manual_backup'),
    path('admin/manual-restore/', admin.site.admin_view(manual_restore), name='manual_restore'),
    path('admin/', admin.site.urls),
    path('search/api/', search_api, name='search_api'),  # Keep API for global search
    path('pages/', page_list, name='page_list'),
    path('pages/<slug:slug>/', page_detail, name='page_detail'),
    # Legacy URLs for backwards compatibility
    path('posts/', RedirectView.as_view(pattern_name='page_list', permanent=True)),
    path('posts/<slug:slug>/', RedirectView.as_view(pattern_name='page_detail', permanent=True)),
    path('VitaminDWiki', RedirectView.as_view(pattern_name='homepage', permanent=True)),
    path('VitaminDWiki/', RedirectView.as_view(pattern_name='homepage', permanent=True)),
    path('tags/', include('tags.urls')),  # Tags filtering
    path('markdownx/', include('markdownx.urls')),

    # Page routes (must come last to avoid catching other URLs)
    path('', homepage, name='homepage'),  # Homepage
    path('<slug:slug>/', site_page_detail, name='site_page_detail'),  # Other site pages
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
