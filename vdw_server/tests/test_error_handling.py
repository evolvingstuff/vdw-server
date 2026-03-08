from django.http import HttpResponse
from django.test import Client, SimpleTestCase, override_settings
from django.urls import path


def explode(_request):
    raise RuntimeError("boom")


def healthy(_request):
    return HttpResponse("ok")


handler500 = "vdw_server.views.custom_server_error"

urlpatterns = [
    path("boom/", explode),
    path("healthy/", healthy),
]


@override_settings(DEBUG=False, ROOT_URLCONF="vdw_server.tests.test_error_handling")
class ErrorHandlingTests(SimpleTestCase):
    def test_unhandled_exception_logs_request_context_and_renders_custom_500(self):
        client = Client()
        client.raise_request_exception = False

        with self.assertLogs("vdw_server.request", level="ERROR") as captured:
            response = client.get("/boom/")

        self.assertEqual(response.status_code, 500)
        self.assertContains(response, "VitaminDWiki hit a server error", status_code=500)
        self.assertIn("X-Request-ID", response.headers)
        self.assertContains(response, response.headers["X-Request-ID"], status_code=500)

        log_output = "\n".join(captured.output)
        self.assertIn("Unhandled exception", log_output)
        self.assertIn("path=/boom/", log_output)
        self.assertIn("request_id=", log_output)

    def test_request_id_header_is_added_to_successful_responses(self):
        response = self.client.get("/healthy/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"ok")
        self.assertIn("X-Request-ID", response.headers)
