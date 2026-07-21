class PublicUpdateAdminMixin:
    minor_edit_post_name = "_save_minor"

    def changeform_view(self, request, object_id=None, form_url="", extra_context=None):
        context = dict(extra_context or {})
        context["show_save_as_minor"] = object_id is not None
        return super().changeform_view(request, object_id, form_url, context)

    def save_model(self, request, obj, form, change):
        is_minor_edit = self.minor_edit_post_name in request.POST
        if is_minor_edit:
            assert change, "Minor edits require an existing object"
            assert hasattr(obj, "public_modified_date"), (
                f"{type(obj).__name__} must define public_modified_date"
            )
            obj.save(update_public_modified_date=False)
            return

        super().save_model(request, obj, form, change)
