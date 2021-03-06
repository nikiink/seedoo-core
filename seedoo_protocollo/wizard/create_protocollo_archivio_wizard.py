# -*- coding: utf-8 -*-
# This file is part of Seedoo.  The COPYRIGHT file at the top level of
# this module contains the full copyright notices and license terms.

import logging, os
from openerp.osv import fields, osv, orm
from openerp.tools.translate import _
import datetime


_logger = logging.getLogger(__name__)


class protocollo_archivio_wizard(osv.TransientModel):
    """
        A wizard to manage the creation/update of Archivio Protocollo
    """
    _name = 'protocollo.archivio.wizard'
    _description = 'Crea Archivio Protocollo'

    def get_interval_type(self, cr, uid, context=None):
        interval_options = [('date', 'Intervallo per data'),('number', 'Intervallo per numero')]

        if context and 'archive_exists' in context and context['archive_exists']:
            return interval_options

        interval_options.insert(0, ('none', 'Nessuna archiviazione'))
        return interval_options

    def get_protocol_year(self, cr, uid, context=None):
        protocollo_archivio_obj = self.pool.get('protocollo.archivio')
        archivio_ids = protocollo_archivio_obj._get_archivio_ids(cr, uid, True)
        archivio_ids_str = ', '.join(map(str, archivio_ids))
        aoo_id = self.get_default_aoo_id(cr, uid, [])
        cr.execute('''
            SELECT DISTINCT(pp.year)
            FROM protocollo_protocollo pp
            WHERE pp.aoo_id = %s AND pp.archivio_id IN (''' + archivio_ids_str + ''')
        ''', (aoo_id,))
        year_res = []
        [year_res.append((str(res[0]), str(res[0]))) for res in cr.fetchall()]
        return year_res

    _columns = {
        'archive_exists': fields.boolean('Archivio esistente'),
        'name': fields.char('Nome Archivio', size=256, readonly=False),
        'archivio_id': fields.many2one('protocollo.archivio', 'Archivio', readonly=False, domain="[('is_current','=',False)]"),
        'aoo_id': fields.many2one('protocollo.aoo', 'AOO', required=True, readonly=True),
        'interval_type': fields.selection(get_interval_type, 'Tipo di intervallo', required=True),
        'date_start': fields.datetime('Data Inizio', required=False),
        'date_end': fields.datetime('Data Fine', required=False),
        'year_start': fields.selection(get_protocol_year, 'Anno Inizio', required=False),
        'year_end': fields.selection(get_protocol_year, 'Anno Fine', required=False),
        'protocol_start': fields.char('Protocollo Inizio', required=False),
        'protocol_end': fields.char('Protocollo Fine', required=False),
    }

    def get_default_archive_exists(self, cr, uid, context):
        if context and 'archive_exists' in context and context['archive_exists']:
            return True
        return False

    def get_default_name(self, cr, uid, wizard):
        return "Archivio di Deposito"

    def get_default_archivio_id(self, cr, uid, context):
        if context and 'archive_exists' in context and context['archive_exists']:
            if context['archivio_id']:
                return context['archivio_id']
        return False

    def get_default_interval_type(self, cr, uid, context):
        if context and 'archive_exists' in context and context['archive_exists']:
            return False
        return 'none'

    def get_default_year_start(self, cr, uid, context):
        return self.get_default_limits(cr, uid, 'year', 'start', context)

    def get_default_year_end(self, cr, uid, context):
        return self.get_default_limits(cr, uid, 'year', 'end', context)

    def get_default_protocol_start(self, cr, uid, context):
        return self.get_default_limits(cr, uid, 'name', 'start', context)

    def get_default_protocol_end(self, cr, uid, context):
        return self.get_default_limits(cr, uid, 'name', 'end', context)

    def get_default_aoo_id(self, cr, uid, context):
        aoo_ids = self.pool.get('protocollo.aoo').search(cr, uid, [])
        for aoo_id in aoo_ids:
            check = self.pool.get('protocollo.aoo').is_visible_to_protocol_action(cr, uid, aoo_id)
            if check:
                return aoo_id
        return False

    def get_default_limits(self, cr, uid, lim_field, lim_type, context):
        if lim_type == 'end':
            ord = 'name desc'
        else:
            ord = 'name asc'
        protocollo_obj = self.pool.get('protocollo.protocollo')
        protocollo_archivio_obj = self.pool.get('protocollo.archivio')
        aoo_id = self.get_default_aoo_id(cr, uid, [])
        archivio_corrente = protocollo_archivio_obj._get_archivio_ids(cr, uid, True)
        start_protocol_ids = protocollo_obj.search(cr, uid, [
            ('aoo_id', '=', aoo_id),
            ('state', 'in', ['registered', 'notified', 'sent', 'waiting', 'error', 'canceled']),
            ('archivio_id', 'in', archivio_corrente)
        ], order=ord, limit=1)
        if len(start_protocol_ids) > 0:
            start_protocol = protocollo_obj.browse(cr, uid, start_protocol_ids[0])
            if lim_field == 'name':
                return start_protocol.name
            else:
                return str(start_protocol.year)
        return False


    _defaults = {
        'archive_exists': get_default_archive_exists,
        'name': get_default_name,
        'archivio_id': get_default_archivio_id,
        'interval_type': get_default_interval_type,
        'aoo_id': get_default_aoo_id,
        'year_start': get_default_year_start,
        'year_end': get_default_year_end,
        'protocol_start': get_default_protocol_start,
        'protocol_end': get_default_protocol_end,
    }


    def action_create(self, cr, uid, ids, context=None):
        wizard = self.browse(cr, uid, ids[0], context)
        protocollo_obj = self.pool.get('protocollo.protocollo')
        protocollo_archivio_obj = self.pool.get('protocollo.archivio')
        archivio_request_obj = self.pool.get('protocollo.archivio.request')

        #Selezione dei protocolli da archiviare
        if wizard.interval_type in ['date', 'number']:
            if wizard.interval_type == 'date':
                if wizard.date_start > wizard.date_end:
                    raise orm.except_orm(_("Avviso"), _("La data di fine deve essere successiva alla data di inizio"))
            elif wizard.interval_type == 'number':
                protocol_start_ids = protocollo_obj.search(cr, uid, [('aoo_id', '=', wizard.aoo_id.id), ('name', '=', wizard.protocol_start)])
                protocol_end_ids = protocollo_obj.search(cr, uid, [('aoo_id', '=', wizard.aoo_id.id), ('name', '=', wizard.protocol_end)])
                if len(protocol_start_ids) != 1 or len(protocol_end_ids) != 1:
                    raise orm.except_orm(_("Avviso"), _("Il numero dell'ultimo protocollo deve essere successivo a quello di inizio"))

        #Selezione dell'archivio da creazione o da preselezione dal wizard
        if not wizard.archive_exists:
            #Creazione dell'archivio
            vals = {
                'name': wizard.name,
                'aoo_id': wizard.aoo_id.id,
                'is_current': False,
                'date_start': wizard.date_start,
                'date_end': wizard.date_end,
            }
            protocollo_archivio_id = protocollo_archivio_obj.create(cr, uid, vals)
        else:
            # Archivio da wizard
            protocollo_archivio_id = wizard.archivio_id.id

        if wizard.interval_type in ['date', 'number']:
            archivio_request_id = archivio_request_obj.create(cr, uid, {
                'archivio_id': protocollo_archivio_id,
                'aoo_id': wizard.aoo_id.id,
                'interval_type': wizard.interval_type,
                'date_start': wizard.date_start if wizard.interval_type == 'date' else False,
                'date_end': wizard.date_end if wizard.interval_type == 'date' else False,
                'year_start': wizard.year_start if wizard.interval_type == 'number' else False,
                'year_end': wizard.year_end if wizard.interval_type == 'number' else False,
                'protocol_start': wizard.protocol_start if wizard.interval_type == 'number' else False,
                'protocol_end': wizard.protocol_end if wizard.interval_type == 'number' else False,
                'state': 'waiting'
            })

            archivio_request_obj.run(cr, uid, [archivio_request_id])

        return {
            'name': 'Archivio',
            'view_type': 'form',
            'view_mode': 'form,tree,kanban',
            'res_model': 'protocollo.archivio',
            'res_id': protocollo_archivio_id,
            'target': 'current',
            'type': 'ir.actions.act_window',
        }