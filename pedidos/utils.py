#!/usr/bin/env python
# -*- coding: utf-8 -*-
from django.db.models import Q, Count
import datetime
from organizaciones import models as Omodels
from medicamentos import models as mmodels
from pedidos import models, config
import collections
from collections import OrderedDict
from django.db.models import Sum
from facturacion import models as factmodels
import itertools
from django.db.models import Avg, Max, Min, Sum
import time
import re

# **********************
# FUNCIONES COMPARTIDAS
# **********************

def crear_pedido_para_sesion(m, pedido):
    p = pedido.to_json()
    p['nroPedido'] = get_next_nro_pedido(m)
    return p

def get_next_nro_pedido(m):
    nro = 1
    try:
        nro = m.objects.latest('nroPedido').nroPedido + 1
    except m.DoesNotExist:
        pass
    return nro

def existe_medicamento_en_pedido(detalles, id_med):
    for detalle in detalles:
        if detalle['medicamento']['id'] == id_med:  # no puede haber dos detalles con el mismo medicamento
            return True
    return False

def crear_detalle_json(detalle, renglon):
    d = detalle.to_json()
    d['renglon'] = renglon
    return d

# **********************
# PEDIDO DE FARMACIA
# **********************

def procesar_pedido_de_farmacia(pedido):

    detalles = models.DetallePedidoDeFarmacia.objects.filter(pedidoDeFarmacia=pedido.nroPedido)  # obtengo todos los detalles del pedido
    if not es_pendiente(pedido):
        remito = models.RemitoDeFarmacia(pedidoFarmacia=pedido, fecha=pedido.fecha)
        remito.save()
        esEnviado = True
        for detalle in detalles:
            esEnviado = procesar_detalle_de_farmacia(detalle, remito, pedido) and esEnviado

        pedido.estado = "Enviado" if esEnviado else "Parcialmente Enviado"
    else:
        pedido.estado = "Pendiente"
        for detalle in detalles:
            detalle.cantidadPendiente = detalle.cantidad
            detalle.save()
    pedido.save()


# FUNCIONES INTERNAS PEDIDO DE FARMACIA

def procesar_detalle_de_farmacia(detalle, remito, pedido):

    stockTotal = detalle.medicamento.get_stock()#Este es el stock que esta en drogueria
    lotes = mmodels.Lote.objects.filter(medicamento__id=detalle.medicamento.id).order_by('fechaVencimiento')

    if stockTotal < detalle.cantidad:#Si el stock total es menor que la cantidad requerida en un reglon del detalle.

        for lote in lotes:#Se recorren todos los lotes del medicamento
            if lote.stock:  # Solo uso lotes que no esten vacios

                stockFyF = lote.stockFarmaYfarmacias
                stockEnFarma = stockFyF.stockFarma
                cantidadTomadaDeLote = lote.stock
                lote.stock = 0
                stockEnFarma -= cantidadTomadaDeLote# Se resta lo que se quito del lote en cuestion (en este caso se quita toda).
                stockFyF.stockFarma = stockEnFarma
                stockFyF.stockFarmacias = stockFyF.stockFarmacias + cantidadTomadaDeLote
                stockFyF.save()
                crearRenglonStockDistribuido(lote,pedido,cantidadTomadaDeLote)
                lote.save()
                #===============================================================
                crearRenglonDetalleRemito(lote,remito,detalle,cantidadTomadaDeLote)#***********

        detalle.cantidadPendiente = detalle.cantidad-stockTotal
        detalle.save()
        return False
    else:
        detalle.cantidadPendiente = 0  # Porque hay stock suficiente para el medicamento del detalle
        detalle.save()  # Actualizo cantidad pendiente antes calculada
        cantidadNecesaria = detalle.cantidad
        i = 0
        while cantidadNecesaria > 0:
            lote = lotes[i]
            if lote.stock:  # Solo uso lotes que no esten vacios
                stockFyF = lote.stockFarmaYfarmacias
                stockEnFarma = stockFyF.stockFarma# Obtengo la cantidad total que suman los lotes o que suma el lote en farma

                if lote.stock < cantidadNecesaria:  # el lote no tiene toda la cantidad que necesito

                    cantidadNecesaria -= lote.stock
                    cantidadTomadaDeLote = lote.stock
                    stockEnFarma -= cantidadTomadaDeLote# Se resta lo que se quito del lote en cuestion (en este caso se quita toda).
                    stockFyF.stockFarma = stockEnFarma
                    stockFyF.stockFarmacias = stockFyF.stockFarmacias + cantidadTomadaDeLote
                    lote.stock = 0
                    crearRenglonStockDistribuido(lote,pedido,cantidadTomadaDeLote)

                else:

                    lote.stock = lote.stock - cantidadNecesaria
                    stockEnFarma = stockEnFarma - cantidadNecesaria
                    stockFyF.stockFarma=stockEnFarma
                    stockFyF.stockFarmacias = stockFyF.stockFarmacias + cantidadNecesaria
                    crearRenglonStockDistribuido(lote,pedido,cantidadNecesaria)
                    cantidadTomadaDeLote = cantidadNecesaria
                    cantidadNecesaria = 0

                crearRenglonDetalleRemito(lote,remito,detalle,cantidadTomadaDeLote)#***********

                lote.save()  # actualizo el stock del lote
                stockFyF.save()
            i += 1

        return True

def crearRenglonStockDistribuido(lote,pedido,cantidadNecesaria):

    idFarmacia=pedido.farmacia.pk
    farmacia=Omodels.Farmacia.objects.get(pk=idFarmacia)
    idLote=lote.pk
    #si la farmacia y lote ya estan en la lista de distribuidos debe recuperarse:

    farmaciaYLoteEnStockDist=mmodels.StockDistribuidoEnFarmacias.objects.filter(lote=lote,farmacia=pedido.farmacia)
    if farmaciaYLoteEnStockDist:
        existeStockDist=True
    else:
        existeStockDist=False

    if existeStockDist:
        stocDist=farmaciaYLoteEnStockDist[0]#Se recupera farmacia con lote en la lista de stock distribuido
    else:
        #Se obtiene el stock distribuido predeterminado al crearse, que es una lista de un solo elemento:
        #Esto pasa por que se esta por usar un lote nuevo recien recibido.
        listStocDist=mmodels.StockDistribuidoEnFarmacias.objects.filter(lote__pk=idLote)
        stocDist=listStocDist[0]#Se obtiene el primer elemento de la lista,(siempre va a existir se crea de forma
                                #predeterminada).

    if stocDist.cantidad == 0:#Si en la cantidad figura cero quiere decir que hay que setear el elemento
                              #predeterminado.
        stocDist.cantidad = stocDist.cantidad + cantidadNecesaria
        stocDist.lote=lote
        stocDist.farmacia=farmacia
    else:
        if not existeStockDist:#Si no hay que crear un nuevo renglon SI ES NECESARIO y luego setear
            stocDist=mmodels.StockDistribuidoEnFarmacias()#Nuevo renglon

        stocDist.cantidad = stocDist.cantidad + cantidadNecesaria
        stocDist.lote=lote
        stocDist.farmacia=farmacia

    stocDist.save()
    #=======================================FIN MODULARIZAR===========================

def crearRenglonDetalleRemito(lote,remito,detalle,cantidadTomadaDeLote):

    detalleRemito = models.DetalleRemitoDeFarmacia()
    detalleRemito.remito = remito
    detalleRemito.set_detalle_pedido(detalle)
    detalleRemito.lote = lote
    detalleRemito.cantidad = cantidadTomadaDeLote
    detalleRemito.save()




def es_pendiente(pedido):#Si el pedido esta pendiente significa que todos sus detalles estan pendientes
                         #por lo tanto si UN renglon cualquiera del detalle tiene stock ya no es pendiente.
    detalles = models.DetallePedidoDeFarmacia.objects.filter(pedidoDeFarmacia=pedido.nroPedido)  # obtengo todos los detalles del pedido
    for detalle in detalles:
        if detalle.medicamento.get_stock() > 0:
            return False
    return True


# **********************
# PEDIDO DE CLINICA
# **********************

def get_medicamentos_con_stock():
    medicamentos_con_stock = []
    lt = datetime.date.today() + datetime.timedelta(weeks=config.SEMANAS_LIMITE_VENCIDOS)
    medicamentos = mmodels.Medicamento.objects.filter(lote__fechaVencimiento__gt=lt)
    for medicamento in medicamentos:
        lotes = mmodels.Lote.objects.filter(medicamento=medicamento)
        if lotes.count() > 0:
            hayStock = False
            for lote in lotes:
                if lote.stock > 0:
                    hayStock = True
                    break
            if hayStock:
                medicamentos_con_stock.append(medicamento.id)
    return mmodels.Medicamento.objects.filter(pk__in=medicamentos_con_stock)


def procesar_pedido_de_clinica(pedido):
    detalles = models.DetallePedidoDeClinica.objects.filter(pedidoDeClinica=pedido.nroPedido) #Obtiene todos los detalles del pedido
    remito = models.RemitoDeClinica(pedidoDeClinica=pedido, fecha=pedido.fecha)
    remito.save()
    for detalle in detalles:
        procesar_detalle_de_clinica(detalle, remito)


# FUNCIONES INTERNAS PEDIDO DE CLINICA

def procesar_detalle_de_clinica(detalle, remito):
    lotes = mmodels.Lote.objects.filter(medicamento__id=detalle.medicamento.id).order_by('fechaVencimiento')
    detalle.cantidadPendiente = 0  # porque hay stock suficiente para el medicamento del detalle
    detalle.save()  # actualizo cantidad pendiente antes calculada
    cantidadNecesaria = detalle.cantidad
    i = 0
    while cantidadNecesaria > 0:
        lote = lotes[i]
        if lote.stock:  # Solo uso lotes que no esten vacios
            cantidadTomadaDeLote = 0
            if lote.stock < cantidadNecesaria:  # el lote no tiene toda la cantidad que necesito
                cantidadNecesaria -= lote.stock
                cantidadTomadaDeLote = lote.stock
                lote.stock = 0
            else:
                lote.stock = lote.stock - cantidadNecesaria
                cantidadTomadaDeLote = cantidadNecesaria
                cantidadNecesaria = 0

            detalleRemito = models.DetalleRemitoDeClinica()
            detalleRemito.remito = remito
            detalleRemito.detallePedidoDeClinica = detalle
            detalleRemito.lote = lote
            detalleRemito.cantidad = cantidadTomadaDeLote
            detalleRemito.save()
            lote.save()  # actualizo el stock del lote
        i += 1


# **********************
# PEDIDO A LABORATORIO
# **********************

def get_next_nro_pedido_laboratorio(m, nombrePk):
    nro = None
    try:
        nro = m.objects.latest(nombrePk).numero + 1
    except m.DoesNotExist:
        nro = 1
    return nro


def get_detalles_a_pedir(pkLaboratorio):
    detalles_a_pedir = []
    pedidos = models.PedidoDeFarmacia.objects.filter(Q(estado='Pendiente') | Q(estado='Parcialmente Enviado'))
    
    for pedido in pedidos:
        detalles = models.DetallePedidoDeFarmacia.objects.filter(Q(pedidoDeFarmacia=pedido.pk) & Q(estaPedido=False) & Q(cantidadPendiente__gt=0) & Q(medicamento__laboratorio=pkLaboratorio))
        for detalle in detalles:
            # creo el detalle del pedido a laboratorio asociado al detalle pedido de farmacia
            detallePedidoAlaboratorio = models.DetallePedidoAlaboratorio()
            detallePedidoAlaboratorio.medicamento = detalle.medicamento
            detallePedidoAlaboratorio.cantidad = detalle.cantidadPendiente 
            detallePedidoAlaboratorio_json = crear_detalle_json(detallePedidoAlaboratorio, len(detalles_a_pedir) + 1)
            detallePedidoAlaboratorio_json['detallePedidoFarmacia'] = detalle.id
            detalles_a_pedir.append(detallePedidoAlaboratorio_json)
    return detalles_a_pedir


def cancelar_pedido_a_laboratorio(pedido):
    if pedido.estado == "Pendiente":
        detallesPedidoAlaboratorio = models.DetallePedidoAlaboratorio.objects.filter(pedido=pedido)
        for detallePedidoAlaboratorio in detallesPedidoAlaboratorio:
            detallePedidoDeFarmacia = detallePedidoAlaboratorio.detallePedidoFarmacia
            if detallePedidoDeFarmacia:
                detallePedidoDeFarmacia.estaPedido = False
                detallePedidoDeFarmacia.save()
        pedido.estado = "Cancelado"
        pedido.save()


def existe_medicamento_en_detalle_suelto(detalles, id_medicamento):
    for detalle in detalles:
        if detalle['detallePedidoFarmacia'] == -1 and detalle['medicamento']['id'] == id_medicamento:
            return True
    return False


# *******************************
# RECEPCION PEDIDO A LABORATORIO
# *******************************

def cargar_detalles(id_pedido, session):
    detalles = models.DetallePedidoAlaboratorio.objects.filter(pedido=id_pedido, cantidadPendiente__gt=0)
    recepcionPedidoAlaboratorio = {'nuevosLotes': {}, 'actualizarLotes': {}, 'detalles': []}
    for detalle in detalles:
        infoDetalle = detalle.to_json()
        infoDetalle['actualizado'] = False  # cuando se actualize el detalle este campo es True
        recepcionPedidoAlaboratorio['detalles'].append(infoDetalle)

    session['recepcionPedidoAlaboratorio'] = recepcionPedidoAlaboratorio


def medicamento_tiene_lotes(medicamento, lotesSesion):
    lt = datetime.date.today() + datetime.timedelta(weeks=config.SEMANAS_LIMITE_VENCIDOS)
    if mmodels.Lote.objects.filter(medicamento=medicamento, fechaVencimiento__gt=lt).count() > 0:
        return True
    for numeroLote, infoLote in lotesSesion.items():
        if infoLote['medicamento'] == medicamento.id:
            return True
    return False


def hay_cantidad_pendiente(detalles, id_detalle):
    posDetalle = get_pos_detalle(detalles, id_detalle)
    detalle = detalles[posDetalle]
    return detalle['cantidadPendiente'] > 0


def get_pos_detalle(detalles, id_detalle):
    i = 0
    for detalle in detalles:
        if detalle['renglon'] == int(id_detalle):
            return i
        i += 1
    return -1


def guardar_recepcion_detalle(session, detalle, infoRecepcionDetalle):
    recepcionPedidoAlaboratorio = session['recepcionPedidoAlaboratorio']
    detallesRemitoRecepcion= session['remitoRecepcion']['detalles']
    detalles = recepcionPedidoAlaboratorio['detalles']
    posDetalle = get_pos_detalle(detalles, detalle.renglon)
    infoDetalle = detalles[posDetalle]
    numeroLote = str(infoRecepcionDetalle['lote'])
    # informacion del detalle de remito

    agregarDetalleRemito = True
    for detalleRemito in detallesRemitoRecepcion:
        if detalleRemito['detallePedidoLaboratorio'] == detalle.pk and detalleRemito['lote'] == numeroLote:
            detalleRemito['cantidad'] += infoRecepcionDetalle['cantidad']
            agregarDetalleRemito = False
            break
            
    if agregarDetalleRemito:
        detallesRemitoRecepcion.append({'detallePedidoLaboratorio':detalle.pk, 'lote': numeroLote, 'cantidad': infoRecepcionDetalle['cantidad']})

    session['remitoRecepcion']['detalles']=detallesRemitoRecepcion

    cantidadStockLote = 0
    if infoDetalle['detallePedidoFarmacia'] == -1:
        cantidadStockLote = infoRecepcionDetalle['cantidad']

    if numeroLote in recepcionPedidoAlaboratorio['nuevosLotes']:
        lote = recepcionPedidoAlaboratorio['nuevosLotes'][numeroLote]
        lote['stock'] += cantidadStockLote
        recepcionPedidoAlaboratorio['nuevosLotes'][numeroLote] = lote  # guardo cambios
    else:
        if numeroLote in recepcionPedidoAlaboratorio['actualizarLotes']:
            stock = recepcionPedidoAlaboratorio['actualizarLotes'][numeroLote]
            stock += cantidadStockLote
            recepcionPedidoAlaboratorio['actualizarLotes'][numeroLote] = stock  # guardo cambios
        else:
            recepcionPedidoAlaboratorio['actualizarLotes'][numeroLote] = cantidadStockLote

    infoDetalle['cantidadPendiente'] -= infoRecepcionDetalle['cantidad']
    infoDetalle['actualizado'] = True
    
    detalles[posDetalle] = infoDetalle  # guardo cambios
    recepcionPedidoAlaboratorio['detalles'] = detalles  # guardo cambios

    session['recepcionPedidoAlaboratorio'] = recepcionPedidoAlaboratorio  # guardo todos los cambios


def guardar_recepcion_detalle_con_nuevo_lote(session, detalle, infoRecepcionDetalle):
    recepcionPedidoAlaboratorio = session['recepcionPedidoAlaboratorio']
    detalles = recepcionPedidoAlaboratorio['detalles']
    detallesRemitoRecepcion = session['remitoRecepcion']['detalles']
    posDetalle = get_pos_detalle(detalles, detalle.renglon)
    infoDetalle = detalles[posDetalle]

    numeroLote = str(infoRecepcionDetalle['lote'])
    cantidadStockLote = 0
    if infoDetalle['detallePedidoFarmacia'] == -1:
        cantidadStockLote = infoRecepcionDetalle['cantidad']
    nuevoLote = {
        'fechaVencimiento': infoRecepcionDetalle['fechaVencimiento'].strftime('%d/%m/%Y'),
        'precio': infoRecepcionDetalle['precio'],
        'stock': cantidadStockLote,
        'medicamento': detalle.medicamento.id
    }  

    recepcionPedidoAlaboratorio['nuevosLotes'][numeroLote] = nuevoLote

    infoDetalle['cantidadPendiente'] -= infoRecepcionDetalle['cantidad']
    infoDetalle['actualizado'] = True
    detalles[posDetalle] = infoDetalle

    recepcionPedidoAlaboratorio['detalles'] = detalles

    session['recepcionPedidoAlaboratorio'] = recepcionPedidoAlaboratorio

    agregarDetalleRemito = True
    for detalleRemito in detallesRemitoRecepcion:
        if detalleRemito['detallePedidoLaboratorio'] == detalle.pk and detalleRemito['lote'] == numeroLote:
            detalleRemito['cantidad'] += infoRecepcionDetalle['cantidad']
            agregarDetalleRemito = False
            break

    if agregarDetalleRemito:
        detallesRemitoRecepcion.append({'detallePedidoLaboratorio': detalle.pk, 'lote': numeroLote, 'cantidad': infoRecepcionDetalle['cantidad']})
    session['remitoRecepcion']['detalles'] = detallesRemitoRecepcion


def crear_nuevos_lotes(nuevosLotes):

    for numeroLote, info in nuevosLotes.items():
        lote = mmodels.Lote()
        lote.numero = numeroLote
        lote.fechaVencimiento = datetime.datetime.strptime(info['fechaVencimiento'], '%d/%m/%Y').date()
        lote.precio = info['precio']
        lote.stock = info['stock']
        lote.medicamento = mmodels.Medicamento.objects.get(pk=info['medicamento'])

        if not lote.medicamento.tiene_lotes(): #Si el medicamento no tiene lotes activos
            stockFyF = mmodels.StockFarmayFarmacias()#Se crea un nuevo elemento
            stockFyF.save()
        else:
            lotesm=lote.medicamento.get_lotes_activos()#Si tiene lotes activos
            stockFyF = lotesm[0].stockFarmaYfarmacias#Recupero el primero

        stockFyF.stockFarma += info['stock']
        stockFyF.save()
        lote.stockFarmaYfarmacias=stockFyF
        lote.save()
        #=======================================INSERCION STOCK DISTRIBUIDO=================================
        stockDist = mmodels.StockDistribuidoEnFarmacias()
        stockDist.lote=lote
        stockDist.cantidad=0
        #===================================================================================================
        stockDist.save()
        stockFyF.save()

def actualizar_lotes(lotes):

    for numeroLote, cantidadRecibida in lotes.items():
        if cantidadRecibida > 0:
            lote = mmodels.Lote.objects.get(numero=numeroLote)
            lote.stock += cantidadRecibida
            stockFyF=lote.stockFarmaYfarmacias

            stockFyF.stockFarma += cantidadRecibida
            stockFyF.save()

            lote.stockFarmaYfarmacias=stockFyF
            lote.save()
            stockFyF.save()

def actualizar_pedido(pedido, detalles):

    recepcionDelPedidoCompleta = True
    for detalle in detalles:
        if detalle['actualizado']:
            detalleDb = models.DetallePedidoAlaboratorio.objects.get(pk=detalle['renglon'])
            detalleDb.cantidadPendiente = detalle['cantidadPendiente']
            if detalleDb.cantidadPendiente > 0:
                recepcionDelPedidoCompleta = False  # Porque hay al menos un detalle que aún falta satisfacer
            detalleDb.save()
        else:
            recepcionDelPedidoCompleta = False  # Porque hay al menos un detalle que no acusó ningún tipo de recibo

    if recepcionDelPedidoCompleta:
        pedido.estado = "Completo"
    else:
        pedido.estado = "Parcialmente Recibido"
    pedido.save()


def actualizar_pedidos_farmacia(remitoLab):

    detalles = models.DetalleRemitoLaboratorio.objects.filter(remito=remitoLab)
    # todos los pedidos de farmacia a los que se les realiza el remito y que luego deben actualizar su estado
    listaPedidosDeFarmacia = []
    remitosDeFarmacia = {}
    for detalle in detalles:
        detallePedidoFarmacia = detalle.detallePedidoLaboratorio.detallePedidoFarmacia

        if detallePedidoFarmacia:
            detallesRemito = remitosDeFarmacia.setdefault(detallePedidoFarmacia.pedidoDeFarmacia.nroPedido, [])
            detallesRemito.append(detalle)
            # detallesRemito[detallePedidoFarmacia.pedidoDeFarmacia.nroPedido] = detallesRemito

    for pkPedido, detallesRemitoLaboratorio in remitosDeFarmacia.items():
        pedidoDeFarmacia = models.PedidoDeFarmacia.objects.get(pk=pkPedido)
        listaPedidosDeFarmacia.append(pedidoDeFarmacia)
        remitoFarmacia = models.RemitoDeFarmacia()
        remitoFarmacia.pedidoFarmacia = pedidoDeFarmacia
        remitoFarmacia.fecha = remitoLab.fecha
        remitoFarmacia.save()

        for detalle in detallesRemitoLaboratorio:
            # actualiza la cantidad pendiente del detalle pedido farmacia
            detallePedidoFarmacia = models.DetallePedidoDeFarmacia.objects.get(pk=detalle.detallePedidoLaboratorio.detallePedidoFarmacia.pk)

            #========================INSERCION PARA STOCK DISTRIBUIDO===========================

            stockFyF = detalle.lote.stockFarmaYfarmacias
            #stockDist = mmodels.StockDistribuidoEnFarmacias.objects.get(lote=detalle.lote)
            listStockDist = mmodels.StockDistribuidoEnFarmacias.objects.filter(lote=detalle.lote)
            #Se obtiene la lista de stockdistribuido correspondiente al lote, si es un lote nuevo
            #el primer elemento no tendra farmacia asignada, este es el elemento que se crea por
            #defecto al momento de crear un lote por lo tanto debo usarlo, de lo contrario aparecera
            #en la lista como un elemento vacio, luego cuando se verifica si ese elemeto fue usado
            #paso a crear o instanciar nuevos elemento para stock distribuido.

            stockDistExistente = mmodels.StockDistribuidoEnFarmacias.objects.filter(lote=detalle.lote,farmacia=pedidoDeFarmacia.farmacia)
            #Se debe verificar que el stockDist no exista previamente si es asi se obtiene y se actualiza sin crear uno nuevo
            if stockDistExistente:
                stockDist=stockDistExistente[0]
            else:
                if listStockDist[0].farmacia is None:
                    stockDist=listStockDist[0]#Uso el creado por defecto
                else:
                    stockDist = mmodels.StockDistribuidoEnFarmacias()#Creo nuevos elementos si es necesario

            #======================FIN INSERCION PARA STOCK DISTRIBUIDO=========================

            detallePedidoFarmacia.cantidadPendiente -= detalle.cantidad

            #========================INSERCION PARA STOCK DISTRIBUIDO===========================

            stockDist.lote = detalle.lote
            stockDist.cantidad += detalle.cantidad
            stockDist.farmacia=pedidoDeFarmacia.farmacia
            stockFyF.stockFarmacias += detalle.cantidad
            stockDist.save()
            stockFyF.save()
            #=======================FIN INSERCION PARA STOCK DISTRIBUIDO========================

            detallePedidoFarmacia.save()

            detalleRemitoFarmacia = models.DetalleRemitoDeFarmacia()
            detalleRemitoFarmacia.cantidad = detalle.cantidad
            detalleRemitoFarmacia.lote = detalle.lote
            detalleRemitoFarmacia.detallePedidoDeFarmacia = detallePedidoFarmacia
            detalleRemitoFarmacia.remito = remitoFarmacia
            detalleRemitoFarmacia.save()

    # se actualiza el estado del pedido de farmacia
    for pedido in listaPedidosDeFarmacia:
        cantidadTotalDetalles = models.DetallePedidoDeFarmacia.objects.filter(pedidoDeFarmacia=pedido).count()
        cantidadDetallesCompletamenteSatisfechos = models.DetallePedidoDeFarmacia.objects.filter(pedidoDeFarmacia=pedido, cantidadPendiente=0).count()

        if cantidadTotalDetalles == cantidadDetallesCompletamenteSatisfechos:
            pedido.estado = "Enviado"
        else:
            pedido.estado = "Parcialmente Enviado"
        pedido.save()


def procesar_recepcion(sesion, pedido):
    remitoSesion = sesion['remitoRecepcion']['remito']
    detallesRemitoSesion = sesion['remitoRecepcion']['detalles']
    nuevosLotes = sesion['recepcionPedidoAlaboratorio']['nuevosLotes']
    actualizarLotes = sesion['recepcionPedidoAlaboratorio']['actualizarLotes']
    detalles = sesion['recepcionPedidoAlaboratorio']['detalles']

    crear_nuevos_lotes(nuevosLotes)
    actualizar_lotes(actualizarLotes)
    actualizar_pedido(pedido, detalles)

    remito = models.RemitoLaboratorio()
    remito.nroRemito = remitoSesion['nroRemito']
    remito.fecha = datetime.datetime.strptime(remitoSesion['fecha'], '%d/%m/%Y').date()
    remito.laboratorio = pedido.laboratorio
    remito.pedidoLaboratorio = pedido
    remito.save()
    for detalle in detallesRemitoSesion:       
        detalleRemito = models.DetalleRemitoLaboratorio()
        detalleRemito.remito = remito
        detalleRemito.cantidad = detalle['cantidad']
        detalleRemito.lote = mmodels.Lote.objects.get(numero= detalle['lote'])
        detalleRemito.detallePedidoLaboratorio = models.DetallePedidoAlaboratorio.objects.get(pk=detalle['detallePedidoLaboratorio'])
        detalleRemito.save()

    actualizar_pedidos_farmacia(remito)


# ************************
# DEVOLUCION MEDICAMENTOS
# ************************

def procesar_devolucion(laboratorio, lotes, distribuidos):
    remito = models.RemitoMedicamentosVencidos()
    remito.numero = get_next_nro_pedido_laboratorio(models.RemitoMedicamentosVencidos, "numero")
    remito.fecha = datetime.datetime.now()
    remito.laboratorio = laboratorio
    remito.save()

    for lote in lotes:
        if lote.stock > 0:
            detalleRemito = models.DetalleRemitoMedicamentosVencido()
            detalleRemito.remito = remito
            detalleRemito.medicamento = lote.medicamento
            detalleRemito.lote = lote
            detalleRemito.cantidad = lote.stock
            detalleRemito.dependencia = "Farma"

            stockFYF = lote.stockFarmaYfarmacias
            stockFYF.stockFarma -= lote.stock
            stockFYF.save()


        for dist in distribuidos:
            if lote.pk == dist.lote.pk and dist.cantidad >0:
                detalleRemitoFarm = models.DetalleRemitoMedicamentosVencido()
                detalleRemitoFarm.remito = remito
                detalleRemitoFarm.medicamento = lote.medicamento
                detalleRemitoFarm.lote = lote
                detalleRemitoFarm.cantidad = dist.cantidad
                detalleRemitoFarm.dependencia = dist.farmacia.razonSocial

                stockFYF = dist.lote.stockFarmaYfarmacias
                stockFYF.stockFarmacias -= dist.cantidad
                stockFYF.save()


                #dist.lote.stock=0
                #dist.lote.save()
                dist.cantidad=0
                dist.save()
                detalleRemitoFarm.save()



        if lote.stock>0:
            detalleRemito.save()
            lote.stock = 0
            lote.save()

def hay_medicamentos_con_stock():
    medicamentos = mmodels.Medicamento.objects.all()
    for medicamento in medicamentos:
        if medicamento.get_stock() > 0:
            return True     
    return False

#==========================================ESTADISTICAS COMPRAS====================================
def estadisticasCompras(get_filtros, get):

    mfilters = get_filtros(get, factmodels.pieDeFacturaDeProveedor)
    compras = factmodels.pieDeFacturaDeProveedor.objects.filter(**mfilters)
    estadisticas = {}

    totalCompras = 0

    for compra in compras:
        if compra.factura.pedidoRel.laboratorio.razonSocial in estadisticas:
            estadisticas[compra.factura.pedidoRel.laboratorio.razonSocial] += compra.total
        else:
            estadisticas[compra.factura.pedidoRel.laboratorio.razonSocial] = compra.total

        totalCompras += compra.total
    # ordeno y selecciono top10
    top = OrderedDict(sorted(estadisticas.items(), key=lambda t: t[1], reverse=True))
    top10 = dict(itertools.islice(top.items(), 10))
    top10 = OrderedDict(sorted(top10.items(), key=lambda t: t[1], reverse=True))

    estadisticas = {
        'columnChart': {'proveedores': [], 'cantidades': []},
        'pieChart': [],
        'excel': []
    }
    resto = totalCompras
    for proveedor, cantidad in top10.items():

        estadisticas['columnChart']['proveedores'].append(proveedor)
        estadisticas['columnChart']['cantidades'].append(float(cantidad))

        numero=float("%.2f" % (cantidad * 100))

        avg = float("%.2f" % (numero / float(totalCompras)))
        estadisticas['pieChart'].append({'name': proveedor, 'y': avg})

        estadisticas['excel'].append({'proveedor': proveedor, 'cantidad': float(cantidad)})

        resto -= cantidad

    if resto > 0:
        avg = float("%.2f" % ((resto * 100) / float(totalCompras)))
        estadisticas['pieChart'].append({'name': u'otros', 'y': avg})

    return estadisticas

#==========================================ESTADISTICAS VENTAS====================================
def estadisticasVentas(get_filtros, get):

    mfilters = get_filtros(get, factmodels.pieDeFacturaAclinica)
    ventas = factmodels.pieDeFacturaAclinica.objects.filter(**mfilters)
    estadisticas = {}

    totalVentas = 0

    for venta in ventas:
        if venta.factura.pedidoRel.clinica.razonSocial in estadisticas:
            estadisticas[venta.factura.pedidoRel.clinica.razonSocial] += venta.total
        else:
            estadisticas[venta.factura.pedidoRel.clinica.razonSocial] = venta.total

        totalVentas += venta.total
    # ordeno y selecciono top10
    top = OrderedDict(sorted(estadisticas.items(), key=lambda t: t[1], reverse=True))
    top10 = dict(itertools.islice(top.items(), 10))
    top10 = OrderedDict(sorted(top10.items(), key=lambda t: t[1], reverse=True))

    estadisticas = {
        'columnChart': {'clientes': [], 'cantidades': []},
        'pieChart': [],
        'excel': []
    }
    resto = totalVentas
    for cliente, cantidad in top10.items():

        estadisticas['columnChart']['clientes'].append(cliente)
        estadisticas['columnChart']['cantidades'].append(float(cantidad))

        numero=float("%.2f" % (cantidad * 100))

        avg = float("%.2f" % (numero / float(totalVentas)))
        estadisticas['pieChart'].append({'name': cliente, 'y': avg})

        estadisticas['excel'].append({'cliente': cliente, 'cantidad': float(cantidad)})

        resto -= cantidad

    if resto > 0:
        avg = float("%.2f" % ((resto * 100) / float(totalVentas)))
        estadisticas['pieChart'].append({'name': u'otros', 'y': avg})

    return estadisticas


#==========================================================================================================


def top_por_cantidad_medicamentos_farmacia(get_filtros, get):
    mfilters = get_filtros(get, models.PedidoDeFarmacia)
    pedidos = models.PedidoDeFarmacia.objects.filter(**mfilters)
    detalle = models.DetallePedidoDeFarmacia
    estadisticas = {}

    totalMedicamentosVendidos = 0

    for pedido in pedidos:
        totalMedicamentosPedidoActual = (detalle.objects.filter(pedidoDeFarmacia=pedido).aggregate(Sum('cantidad'))).get('cantidad__sum')
        totalMedicamentosVendidos += totalMedicamentosPedidoActual
        farmacia = pedido.farmacia.razonSocial
        if farmacia in estadisticas:
            estadisticas[farmacia] += totalMedicamentosPedidoActual
        else:
            estadisticas[farmacia] = totalMedicamentosPedidoActual

    # ordeno y selecciono top10
    top = OrderedDict(sorted(estadisticas.items(), key=lambda t: t[1], reverse=True))
    top10 = dict(itertools.islice(top.items(), 10))
    top10 = OrderedDict(sorted(top10.items(), key=lambda t: t[1], reverse=True))

    estadisticas = {
        'columnChart': {'farmacias': [], 'cantidades': []},
        'pieChart': [],
        'excel': []
    }

    resto = totalMedicamentosVendidos
    for farmacia, cantidad in top10.items():
        estadisticas['columnChart']['farmacias'].append(farmacia)
        estadisticas['columnChart']['cantidades'].append(cantidad)

        avg = float("%.2f" % ((cantidad * 100) / float(totalMedicamentosVendidos)))
        estadisticas['pieChart'].append({'name': farmacia, 'y': avg})

        estadisticas['excel'].append({'farmacia': farmacia, 'cantidad': cantidad})

        resto -= cantidad
    
    if resto > 0:
        avg = float("%.2f" % ((resto * 100) / float(totalMedicamentosVendidos)))
        estadisticas['pieChart'].append({'name': u'otros', 'y': avg})

    return estadisticas


def top_por_cantidad_pedidos_farmacia(get_filtros, get):
    mfilters = get_filtros(get, models.PedidoDeFarmacia)
    pedidos = models.PedidoDeFarmacia.objects.filter(**mfilters)
    estadisticas = {}

    totalPedidosDeFarmacia = 0

    for pedido in pedidos:
        if pedido.farmacia.razonSocial in estadisticas:
            estadisticas[pedido.farmacia.razonSocial] += 1
        else:
            estadisticas[pedido.farmacia.razonSocial] = 1

        totalPedidosDeFarmacia += 1
    # ordeno y selecciono top10
    top = OrderedDict(sorted(estadisticas.items(), key=lambda t: t[1], reverse=True))
    top10 = dict(itertools.islice(top.items(), 10))
    top10 = OrderedDict(sorted(top10.items(), key=lambda t: t[1], reverse=True))


    estadisticas = {
        'columnChart': {'farmacias': [], 'cantidades': []},
        'pieChart': [],
        'excel': []
    }

    resto = totalPedidosDeFarmacia
    for farmacia, cantidad in top10.items():

        estadisticas['columnChart']['farmacias'].append(farmacia)
        estadisticas['columnChart']['cantidades'].append(cantidad)

        avg = float("%.2f" % ((cantidad * 100) / float(totalPedidosDeFarmacia)))
        estadisticas['pieChart'].append({'name': farmacia, 'y': avg})
        estadisticas['excel'].append({'farmacia': farmacia, 'cantidad': cantidad})

        resto -= cantidad
    
    if resto > 0:
        avg = float("%.2f" % ((resto * 100) / float(totalPedidosDeFarmacia)))
        estadisticas['pieChart'].append({'name': u'otros', 'y': avg})

    return estadisticas


def top_por_cantidad_medicamentos_clinica(get_filtros, get):
    mfilters = get_filtros(get, models.PedidoDeClinica)
    pedidos = models.PedidoDeClinica.objects.filter(**mfilters)
    detalle = models.DetallePedidoDeClinica
    estadisticas = {}

    totalMedicamentosVendidos = 0

    for pedido in pedidos:
        totalMedicamentosPedidoActual = (detalle.objects.filter(pedidoDeClinica=pedido).aggregate(Sum('cantidad'))).get('cantidad__sum')
        totalMedicamentosVendidos += totalMedicamentosPedidoActual
        clinica = pedido.clinica.razonSocial
        if clinica in estadisticas:
            estadisticas[clinica] += totalMedicamentosPedidoActual
        else:
            estadisticas[clinica] = totalMedicamentosPedidoActual

    # ordeno y selecciono top10
    top = OrderedDict(sorted(estadisticas.items(), key=lambda t: t[1], reverse=True))
    top10 = dict(itertools.islice(top.items(), 10))
    top10 = OrderedDict(sorted(top10.items(), key=lambda t: t[1], reverse=True))

    estadisticas = {
        'columnChart': {'clinicas': [], 'cantidades': []},
        'pieChart': [],
        'excel': []
    }

    resto = totalMedicamentosVendidos
    for clinica, cantidad in top10.items():
        estadisticas['columnChart']['clinicas'].append(clinica)
        estadisticas['columnChart']['cantidades'].append(cantidad)

        avg = float("%.2f" % ((cantidad * 100) / float(totalMedicamentosVendidos)))
        estadisticas['pieChart'].append({'name': clinica, 'y': avg})

        estadisticas['excel'].append({'clinica': clinica, 'cantidad': cantidad})

        resto -= cantidad

    if resto > 0:
        avg = float("%.2f" % ((resto * 100) / float(totalMedicamentosVendidos)))
        estadisticas['pieChart'].append({'name': u'otros', 'y': avg})

    return estadisticas


def top_por_cantidad_pedidos_clinica(get_filtros, get):
    mfilters = get_filtros(get, models.PedidoDeClinica)
    pedidos = models.PedidoDeClinica.objects.filter(**mfilters)
    estadisticas = {}

    totalPedidosDeClinica = 0

    for pedido in pedidos:
        if pedido.clinica.razonSocial in estadisticas:
            estadisticas[pedido.clinica.razonSocial] += 1
        else:
            estadisticas[pedido.clinica.razonSocial] = 1

        totalPedidosDeClinica += 1

    # ordeno y selecciono top10
    top = OrderedDict(sorted(estadisticas.items(), key=lambda t: t[1], reverse=True))
    top10 = dict(itertools.islice(top.items(), 10))
    top10 = OrderedDict(sorted(top10.items(), key=lambda t: t[1], reverse=True))

    estadisticas = {
        'columnChart': {'clinicas': [], 'cantidades': []},
        'pieChart': [],
        'excel': []
    }

    resto = totalPedidosDeClinica
    for clinica, cantidad in top10.items():
        estadisticas['columnChart']['clinicas'].append(clinica)
        estadisticas['columnChart']['cantidades'].append(cantidad)

        avg = float("%.2f" % ((cantidad * 100) / float(totalPedidosDeClinica)))
        estadisticas['pieChart'].append({'name': clinica, 'y': avg})

        estadisticas['excel'].append({'clinica': clinica, 'cantidad': cantidad})

        resto -= cantidad

    if resto > 0:
        avg = float("%.2f" % ((resto * 100) / float(totalPedidosDeClinica)))
        estadisticas['pieChart'].append({'name': u'otros', 'y': avg})

    return estadisticas


def top_por_solicitud_medicamentos_laboratorio(get_filtros, get):
    mfilters = get_filtros(get, models.PedidoAlaboratorio)
    pedidos = models.PedidoAlaboratorio.objects.filter(**mfilters)
    detalle = models.DetallePedidoAlaboratorio
    estadisticas = {}

    totalMedicamentosComprados = 0

    for pedido in pedidos:
        totalMedicamentosPedidoActual = (detalle.objects.filter(pedido=pedido).aggregate(Sum('cantidad'))).get('cantidad__sum')
        totalMedicamentosComprados += totalMedicamentosPedidoActual
        laboratorio = pedido.laboratorio.razonSocial
        if laboratorio in estadisticas:
            estadisticas[laboratorio] += totalMedicamentosPedidoActual
        else:
            estadisticas[laboratorio] = totalMedicamentosPedidoActual

    # ordeno y selecciono top10
    top = OrderedDict(sorted(estadisticas.items(), key=lambda t: t[1], reverse=True))
    top10 = dict(itertools.islice(top.items(), 10))
    top10 = OrderedDict(sorted(top10.items(), key=lambda t: t[1], reverse=True))

    estadisticas = {
        'columnChart': {'laboratorios': [], 'cantidades': []},
        'pieChart': [],
        'excel': []
    }

    resto = totalMedicamentosComprados
    for laboratorio, cantidad in top10.items():
        estadisticas['columnChart']['laboratorios'].append(laboratorio)
        estadisticas['columnChart']['cantidades'].append(cantidad)

        avg = float("%.2f" % ((cantidad * 100) / float(totalMedicamentosComprados)))
        estadisticas['pieChart'].append({'name': laboratorio, 'y': avg})

        estadisticas['excel'].append({'laboratorio': laboratorio, 'cantidad': cantidad})

        resto -= cantidad
    
    if resto > 0:
        avg = float("%.2f" % ((resto * 100) / float(totalMedicamentosComprados)))
        estadisticas['pieChart'].append({'name': u'otros', 'y': avg})

    return estadisticas


def top_por_solicitud_pedidos_laboratorio(get_filtros, get):
    mfilters = get_filtros(get, models.PedidoAlaboratorio)
    pedidos = models.PedidoAlaboratorio.objects.filter(**mfilters)
    estadisticas = {}

    totalPedidosAlaboratorio = 0

    for pedido in pedidos:
        if pedido.laboratorio.razonSocial in estadisticas:
            estadisticas[pedido.laboratorio.razonSocial] += 1
        else:
            estadisticas[pedido.laboratorio.razonSocial] = 1

        totalPedidosAlaboratorio += 1

    # ordeno y selecciono top10
    top = OrderedDict(sorted(estadisticas.items(), key=lambda t: t[1], reverse=True))
    top10 = dict(itertools.islice(top.items(), 10))
    top10 = OrderedDict(sorted(top10.items(), key=lambda t: t[1], reverse=True))

    estadisticas = {
        'columnChart': {'laboratorios': [], 'cantidades': []},
        'pieChart': [],
        'excel': []
    }

    resto = totalPedidosAlaboratorio
    for laboratorio, cantidad in top10.items():
        estadisticas['columnChart']['laboratorios'].append(laboratorio)
        estadisticas['columnChart']['cantidades'].append(cantidad)

        avg = float("%.2f" % ((cantidad * 100) / float(totalPedidosAlaboratorio)))
        estadisticas['pieChart'].append({'name': laboratorio, 'y': avg})

        estadisticas['excel'].append({'laboratorio': laboratorio, 'cantidad': cantidad})

        resto -= cantidad
    
    if resto > 0:
        avg = float("%.2f" % ((resto * 100) / float(totalPedidosAlaboratorio)))
        estadisticas['pieChart'].append({'name': u'otros', 'y': avg})

    return estadisticas

def formatearFecha(fecha):
     fechaConv = datetime.datetime.strptime(fecha, "%d/%m/%Y").strftime('%Y-%m-%d')#metodo de datetime
     return fechaConv


#=========================================STOCK DISTRIBUIDO=====================================================

class parametros():
    MAX_A_QUITAR=20 #El maximo a quitar de farmacias.
    MIN_A_DEJAR=5 #El minimo que se debe dejar por lote en una farmacia.
    MARGEN=0#Margen para que existan altas probabilidades de no dejar lotes en cero.


def buscarYobtenerDeFarmacias(detalles,pedido,farmacia,verificar):

    renglones=[]
    for detalle in detalles:#Se recorrern los detalles del pedido
        if detalle.cantidadPendiente > 0:
            cantidadAobtener=detalle.cantidadPendiente
            medicamento = detalle.medicamento
            #Se obtienen todos los lotes distribuidos en farmacias del medicamento que se obtuvo del detalle.
            listStockDist = mmodels.StockDistribuidoEnFarmacias.objects.filter(lote__medicamento__pk=medicamento.pk).order_by('lote__pk')

            #Se obtienen los lotes activos (no vencidos) del medicamento que se obtuvo del detalle.
            lotesActivos=detalle.medicamento.get_lotes_activos()
            #Se recorren los lotes activos del medicamento
            informe_listFarmacias=[]

            for loteActivo in lotesActivos:
                seguirSacandoAlote=True

            #========PARA EL INFORME A PRESENTAR==========
                i=0
                porcion = 0
                while cantidadAobtener > 0 and seguirSacandoAlote:
                    dist = listStockDist[i]
                    if ((loteActivo.pk == dist.lote.pk) and (farmacia.razonSocial != dist.farmacia.razonSocial)):

                        if (dist.cantidad - cantidadAobtener) > parametros.MIN_A_DEJAR:
                            porcion = cantidadAobtener
                        elif(dist.cantidad > parametros.MIN_A_DEJAR):#Si la cantidad no alcanza pero aun asi es mayor al minimo se decrementa esa porcion.
                            porcion = (dist.cantidad - parametros.MIN_A_DEJAR)

                        if porcion > 0:

                            dist.cantidad -= porcion
                            detalle.cantidadPendiente -= porcion
                            nuevoStockDist = mmodels.StockDistribuidoEnFarmacias()
                            nuevoStockDist.cantidad += porcion
                            nuevoStockDist.farmacia=farmacia
                            nuevoStockDist.lote=dist.lote

                            informe_farmaciaRs=dist.farmacia.razonSocial#**********
                            informe_cantidadQuitada = porcion#**********
                            informe_lote=dist.lote.numero#**********
                            informe_nombreFantasia=dist.lote.medicamento#**********
                            informe_listFarmacias.append(informe_farmaciaRs + ';' + str(informe_lote) + ';' + str(informe_cantidadQuitada) + ';' + str(informe_nombreFantasia))
                            cantidadAobtener -= porcion

                            if not verificar:
                                dist.save()
                                detalle.save()
                                nuevoStockDist.save()
                   
                    print "len",len(listStockDist)                
                    if len(listStockDist)-1==i:
                        seguirSacandoAlote = False

                    i += 1

            for inf in informe_listFarmacias:

                data = inf.split(';')
                renglon={}
                renglon["farmacia"]=data[0]
                renglon["lote"]=data[1]
                renglon["totalq"]=data[2]
                renglon["nombreF"]=data[3]

                renglones.append(renglon)#Informe final a presentarse al usuario.

    if not verificar:
        pedido.estado="Enviado"
        pedido.save()

    return renglones


def cantDeLoteActivoDist(loteActivo,listStockDist):
    cantidad=0
    for list in listStockDist:
        if loteActivo.pk==list.lote.pk:
            cantidad += list.cantidad
    return cantidad

def verificarCantidad(detalles):

     haySuficiente=True
     for detalle in detalles:
         cantidadAobtener=detalle.cantidadPendiente
         if cantidadAobtener > 0:
             medicamento = detalle.medicamento
             farmacia=detalle.pedidoDeFarmacia.farmacia#*******
             #El exclude esta por que la farmacia de destino no debe incluirse ella misma en la cuenta
             stockTotalDistDic = mmodels.StockDistribuidoEnFarmacias.objects.filter(lote__medicamento__pk=medicamento.pk).exclude(farmacia=farmacia).aggregate(Sum('cantidad'))
             #Se obtienen todos los registros del stock distribuido cuyo medicamanto sea igual al medicamanto del detalle
             distFarmacias = mmodels.StockDistribuidoEnFarmacias.objects.filter(lote__medicamento__pk=medicamento.pk)
             medicamentos = []#Para insertar medicamentos

             for distFarmacia in distFarmacias:#Se recorren las farmacias del stock dist y se recuperan sus medicamentos
               #***Puede suceder que el medicamento este duplicado en este array por que puede pertenecer a dos lotes distintos
               #y aun asi estar en la misma farmacia por lo que no se debe contar la farmacia dos veces ya que se trata de la misma.
               medicamentos.append(distFarmacia.farmacia.pk)
             duplicados = [x for x, y in collections.Counter(medicamentos).items() if y > 1]
             cantDuplicados = len(duplicados)#Si hay ducplicados el len va a ser mayor a cero indicando la cantidad de duplicados que hay.

             cantidadFarmaciasConElMed = len(medicamentos)-cantDuplicados#El len de medicamentos incluye la cuenta de los duplicados
                                                                         #por lo que se le resta la cantidad de duplicados.
             stockTotalDist = stockTotalDistDic['cantidad__sum']#Esto es por que aggregate devuelve un diccionario.
             totalMinimoAdejar = cantidadFarmaciasConElMed * parametros.MIN_A_DEJAR

             if stockTotalDistDic['cantidad__sum'] is None:
                 stockTotalDist=0


             if (stockTotalDist - cantidadAobtener) >= totalMinimoAdejar:
                respetaMinimo = True
             else:
                respetaMinimo =False

             if ( (stockTotalDist < cantidadAobtener + parametros.MARGEN) or (cantidadAobtener > parametros.MAX_A_QUITAR) or not respetaMinimo):
                haySuficiente = False

     return haySuficiente


