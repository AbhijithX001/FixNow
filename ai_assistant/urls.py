"""URL configuration for the ai_assistant app."""

from django.urls import path

from . import views

app_name = "ai_assistant"

urlpatterns = [
    path("health/", views.health, name="health"),
    path("chat/", views.chat, name="chat"),
]
