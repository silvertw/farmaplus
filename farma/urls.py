"""organizandor URL Configuration

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/1.8/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  url(r'^$', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  url(r'^$', Home.as_view(), name='home')
Including another URLconf
    1. Add an import:  from blog import urls as blog_urls
    2. Add a URL to urlpatterns:  url(r'^blog/', include(blog_urls))
"""
from django.conf.urls import include, url
from django.contrib import admin
from . import views
from medicamentos import views as mviews


urlpatterns = [
    url(r'^admin/', include(admin.site.urls)),
    url(r'^$', views.login, name="login"),
    url(r'^inicio/$', views.inicio, name="inicio"),
    url(r'^altafarmacia/$', views.altafarmacia, name="altafarmacia"),
    url(r'^monodrogas/$', mviews.monodrogas, name="monodrogas"),
    url(r'^monodrogas/add/$', mviews.monodrogas, name="monodroga_add"),
    url(r'^altaMedicamento/$', views.altaMedicamento, name="altaMedicamento"),
    url(r'^pedidoLaboratorio/$', views.pedidoLaboratorio, name="pedidoLaboratorio"),
    url(r'^recepcionPedidoLaboratorio/$', views.recepcionPedidoLaboratorio, name="recepcionPedidoLaboratorio"),
    url(r'^pedidoDeFarmacia/$', views.pedidoDeFarmacia, name="pedidoDeFarmacia"),
    url(r'^pedidoDeClinica/$', views.pedidoDeClinica,name="pedidoDeClinica"),
]
