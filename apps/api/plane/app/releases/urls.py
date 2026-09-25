from django.urls import path
from .api import ReleaseEndpoint, ReleaseOptionsEndpoint, ReleaseActionEndpoint

prefix = "workspaces/<str:slug>/projects/<uuid:project_id>/releases/"
urlpatterns = [
    path(prefix, ReleaseEndpoint.as_view()),
    path(prefix + "options/", ReleaseOptionsEndpoint.as_view()),
    path(prefix + "<uuid:release_id>/", ReleaseEndpoint.as_view()),
    path(prefix + "<uuid:release_id>/publish/", ReleaseActionEndpoint.as_view(), {"action": "publish"}),
    path(prefix + "<uuid:release_id>/unpublish/", ReleaseActionEndpoint.as_view(), {"action": "unpublish"}),
    path(prefix + "<uuid:release_id>/generate/", ReleaseActionEndpoint.as_view(), {"action": "generate"}),
]
