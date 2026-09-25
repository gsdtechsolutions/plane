from django.urls import path
from .api import AppConnectionEndpoint, PageReviewEndpoint, ReviewPagesEndpoint, ReviewCapabilitiesEndpoint

base = "workspaces/<str:slug>/projects/<uuid:project_id>/"
urlpatterns = [
    path(base + "review-capabilities/", ReviewCapabilitiesEndpoint.as_view()),
    path(base + "app-connection/", AppConnectionEndpoint.as_view()),
    path(base + "review-pages/", ReviewPagesEndpoint.as_view()),
    path(base + "page-reviews/", PageReviewEndpoint.as_view()),
    path(base + "page-reviews/<uuid:review_id>/", PageReviewEndpoint.as_view()),
]
