from django.http import HttpResponse
from django.test import RequestFactory, SimpleTestCase, override_settings

from vdw_server.middleware import AdminHttpsOnlyMiddleware


class AdminHttpsOnlyMiddlewareTests(SimpleTestCase):
    def setUp(self):
        self.factory = RequestFactory()

    def _middleware(self):
        return AdminHttpsOnlyMiddleware(lambda request: HttpResponse('ok'))

    @override_settings(ADMIN_REQUIRE_HTTPS=True)
    def test_redirects_insecure_admin_requests(self):
        request = self.factory.get('/admin/login/')

        response = self._middleware()(request)

        self.assertEqual(response.status_code, 301)
        self.assertEqual(response['Location'], 'https://testserver/admin/login/')

    @override_settings(ADMIN_REQUIRE_HTTPS=True)
    def test_allows_secure_admin_requests(self):
        request = self.factory.get('/admin/login/', secure=True)

        response = self._middleware()(request)

        self.assertEqual(response.status_code, 200)

    @override_settings(ADMIN_REQUIRE_HTTPS=True)
    def test_respects_forwarded_proto_header(self):
        request = self.factory.get('/admin/', HTTP_X_FORWARDED_PROTO='https')

        response = self._middleware()(request)

        self.assertEqual(response.status_code, 200)

    @override_settings(ADMIN_REQUIRE_HTTPS=True)
    def test_non_admin_paths_bypass_redirect(self):
        request = self.factory.get('/pages/')

        response = self._middleware()(request)

        self.assertEqual(response.status_code, 200)

    @override_settings(ADMIN_REQUIRE_HTTPS=False)
    def test_disabled_flag_allows_http(self):
        request = self.factory.get('/admin/login/')

        response = self._middleware()(request)

        self.assertEqual(response.status_code, 200)
