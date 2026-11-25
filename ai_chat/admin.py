from django.contrib import admin
from django.shortcuts import redirect
from django.urls import reverse

from .models import ChatSettings, get_chat_settings


@admin.register(ChatSettings)
class ChatSettingsAdmin(admin.ModelAdmin):
    """One record: the list page goes straight to its edit form."""

    fields = ('enabled', 'strictness', 'extra_instructions')

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def changelist_view(self, request, extra_context=None):
        settings_record = get_chat_settings()
        return redirect(reverse('admin:ai_chat_chatsettings_change', args=[settings_record.pk]))
