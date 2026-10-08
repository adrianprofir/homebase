from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import path

from inventory import views

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("apps/<int:pk>/", views.app_detail, name="app_detail"),
    path("accounts/login/", auth_views.LoginView.as_view(), name="login"),
    path("accounts/logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("admin/", admin.site.urls),
]
