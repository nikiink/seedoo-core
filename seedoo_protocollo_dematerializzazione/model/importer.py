# -*- coding: utf-8 -*-
# This file is part of Seedoo.  The COPYRIGHT file at the top level of
# this module contains the full copyright notices and license terms.

import base64
import datetime
import os
from StringIO import StringIO

import pytz
from smb.SMBConnection import SMBConnection
from smb.smb_constants import SMB_FILE_ATTRIBUTE_READONLY, SMB_FILE_ATTRIBUTE_DIRECTORY, SMB_FILE_ATTRIBUTE_ARCHIVE

from openerp import SUPERUSER_ID
from openerp.osv import orm, fields
from openerp.tools import DEFAULT_SERVER_DATETIME_FORMAT as DSDF
from ..utility.ean import EanUtility


class dematerializzazione_importer(orm.Model):
    _name = 'dematerializzazione.importer'

    TIPOLOGIA_SELECTION = [('aggancio', 'Importazione e Aggancio'), ('creazione', 'Importazione e Creazione')]

    def on_change_tipologia_importazione(self, cr, uid, ids, aoo_id, context=None):
        values = {}
        if aoo_id:
            values = {
                # 'aoo_id': False,
                'tipologia_protocollo': False,
                'employee_ids': []
            }
        return {'value': values}

    def on_change_aoo_id(self, cr, uid, ids, aoo_id, context=None):
        values = {}
        if aoo_id:
            values = {
                'tipologia_protocollo': False,
                'employee_ids': []
            }
        return {'value': values}

    _columns = {
        # 'configurazione_id': fields.many2one('dematerializzazione.configurazione', 'Configurazione'),
        'import_attivo': fields.boolean('Import attivo'),
        'title': fields.char('Nome Repository', char=80, required=True),
        'description': fields.text('Descrizione Repository'),
        'tipologia_importazione': fields.selection(TIPOLOGIA_SELECTION, 'Tipologia Importazione', select=True,
                                                   required=True),
        'tipologia_protocollo': fields.many2one('protocollo.typology', 'Tipologia Protocollo'),
        'aoo_id': fields.many2one('protocollo.aoo', 'AOO', required=True),
        # 'employee_id': fields.many2one('hr.employee', 'Protocollatore'),
        'employee_ids': fields.many2many('hr.employee', 'dematerializzazione_importer_employee_rel', 'importer_id',
                                         'employee_id', 'Utenti', required=True),
        'address': fields.char('Indirizzo', char=256, required=True),
        'share': fields.char('Cartella Condivisa', char=256, required=True),
        'path': fields.char('Percorso', char=256, required=True),
        'processed_folder': fields.char('Cartella dei File Processati', char=256, required=True),
        'failed_folder': fields.char('Cartella dei File in Errore', char=256, required=True),
        'auth': fields.boolean('Autenticazione'),
        'smb_domain': fields.char(string='Domain', char=100, required=False),
        'user': fields.char('Username', char=100),
        'password': fields.char('Password', char=100),
        'test_connessione': fields.char('Verify connection', readonly=True),
    }

    def _get_default_aoo_id(self, cr, uid, context=None):
        if context and context.has_key('aoo_id') and context['aoo_id']:
            return context['aoo_id']

    _defaults = {
        'import_attivo': False,
        'aoo_id': _get_default_aoo_id,
        'path': '/',
        'smb_domain': ''
    }

    file_search = SMB_FILE_ATTRIBUTE_READONLY | SMB_FILE_ATTRIBUTE_ARCHIVE
    directory_search = SMB_FILE_ATTRIBUTE_READONLY | SMB_FILE_ATTRIBUTE_ARCHIVE | SMB_FILE_ATTRIBUTE_DIRECTORY

    def _directory_check(self, conn, share, path, directory):
        entries = conn.listPath(share, path, self.directory_search)
        directories = map(lambda e: e.filename, entries)
        if directory not in directories:
            path_directory = os.path.join(path, directory)
            conn.createDirectory(share, path_directory)

    def _log_file_error(self, cr, uid, conn, importer, file_to_import, today_folder, storico_importer_file_id, error):
        share = importer.share

        old_path = os.path.join(importer.path, file_to_import.filename)

        storico_importer_file_obj = self.pool.get('dematerializzazione.storico.importazione.importer.file')
        storico_importer_file_obj.write(cr, uid, [storico_importer_file_id], {
            'esito': 'errore',
            'errore': error
        })

        failed_folder_path = os.path.join(importer.path, importer.failed_folder)
        today_failed_folder_path = os.path.join(failed_folder_path, today_folder)
        new_path = os.path.join(today_failed_folder_path, file_to_import.filename)

        self._directory_check(conn, share, failed_folder_path, today_folder)

        failed_files_entries = conn.listPath(share, today_failed_folder_path)
        failed_files = map(lambda e: e.filename, failed_files_entries)
        if file_to_import.filename in failed_files:
            conn.deleteFiles(share, new_path)
        conn.rename(share, old_path, new_path)

    def _process_protocol_aggancio(self, cr, uid, conn, importer, file_to_import, storico_importer_id):
        protocollo_obj = self.pool.get('protocollo.protocollo')
        attachment_obj = self.pool.get('ir.attachment')

        # Verifica la correttezza del nome del file rispetto alla sintassi dell'Ean13
        ean = os.path.splitext(os.path.basename(file_to_import.filename.encode('utf-8')))[0]
        errore = ''
        protocollo_id = None
        esito = EanUtility.ean_verify(ean)

        if not esito:
            esito = False
            errore = 'Nome del file non valido - Controllo EAN errato'
            return esito, errore, protocollo_id

        protocollo_rif = EanUtility.ean_get_protocollo(ean)

        if not protocollo_rif[0]:
            esito = False
            errore = 'Nome del file non valido - Anno e numero protocollo non conformi'
            return esito, errore, protocollo_id

        protocol_ids = protocollo_obj.search(cr, SUPERUSER_ID, [
            ('year', '=', protocollo_rif[1]),
            ('name', '=', protocollo_rif[2]),
            ('aoo_id', '=', importer.aoo_id.id)
        ])

        if not len(protocol_ids):
            esito = False
            errore = 'Protocollo in ingresso %s - %s non esistente' % (protocollo_rif[1], protocollo_rif[2])
            return esito, errore, protocollo_id

        prot = protocollo_obj.browse(cr, SUPERUSER_ID, protocol_ids[0])
        attachment_ids = attachment_obj.search(cr, SUPERUSER_ID, [
            ('res_model', '=', 'protocollo.protocollo'),
            ('res_id', '=', prot.id)
        ])

        if attachment_ids:
            esito = False
            errore = 'Allegato già presente per il protocollo %s - %s' % (protocollo_rif[1], protocollo_rif[2])
            return esito, errore, protocollo_id

        temp_fh = StringIO()
        file_attributes, filesize = conn.retrieveFile(importer.share, file_to_import.filename, temp_fh)
        data_encoded = base64.encodestring(temp_fh.getvalue())

        if not filesize:
            esito = False
            errore = 'Errore nel caricamento del file'
            return esito, errore, protocollo_id

        protocollo_obj.carica_documento_principale(cr, SUPERUSER_ID, prot.id, data_encoded, file_to_import.filename, "")
        protocollo_obj.associa_importer_protocollo(cr, SUPERUSER_ID, prot.id, storico_importer_id)
        return esito, errore, prot.id

    def _process_protocol_creazione(self, cr, uid, conn, importer, file_to_import, storico_importer_id, import_counter):
        ir_model_data_obj = self.pool.get('ir.model.data')
        gedoc_document_obj = self.pool.get('gedoc.document')
        attachment_obj = self.pool.get('ir.attachment')

        esito = True
        errore = ''
        gedoc_document_id = None

        try:
            temp_fh = StringIO()
            file_attributes, filesize = conn.retrieveFile(importer.share, file_to_import.filename, temp_fh)
            data_encoded = base64.encodestring(temp_fh.getvalue())

            if filesize > 0:
                # now = datetime.datetime.now().strftime(DSDF)
                # name = 'Nuovo Protocollo del ' + now + ' (%s) ' % import_counter
                # vals = {
                #     'name': name,
                #     'typology': importer.tipologia_protocollo.id,
                #     'creation_date': datetime.datetime.now().strftime('%Y%m%d %H:%M:%S'),
                #     'receiving_date': datetime.datetime.now().strftime('%Y%m%d %H:%M:%S'),
                #     'type': 'in',
                #     'aoo_id': importer.aoo_id.id,
                #     'user_id': importer.employee_id.user_id.id,
                #     'mimetype': 'application/pdf',
                #     'importer_id': storico_importer_id
                # }
                # protocollo_obj = self.pool.get('protocollo.protocollo')
                # protocollo_id = protocollo_obj.create(cr, uid, vals, context={})
                # protocollo_obj.carica_documento_principale(cr, uid, protocollo_id, data_encoded, file_to_import.filename, "")

                now = datetime.datetime.now().strftime(DSDF)
                name = 'Nuovo Documento del ' + now + ' (%s) ' % import_counter
                document_type = ir_model_data_obj.get_object(cr, uid,
                                                             'seedoo_protocollo_dematerializzazione',
                                                             'gedoc_document_type_imported')
                vals = {
                    'name': name,
                    'subject': name,
                    'document_type': document_type.id,
                    'main_doc_id': False,
                    'user_id': uid,
                    'data_doc': datetime.datetime.now().strftime('%Y%m%d %H:%M:%S'),
                    'employee_comp_ids': [(6, 0, importer.employee_ids.ids)],
                    'aoo_id': importer.aoo_id.id,
                    'imported': True,
                    'importer_id': storico_importer_id,
                    'typology_id': importer.tipologia_protocollo.id
                }
                gedoc_document_id = gedoc_document_obj.create(cr, uid, vals)

                main_doc_id = attachment_obj.create(cr, uid, {
                    'name': file_to_import.filename,
                    'datas': data_encoded,
                    'datas_fname': file_to_import.filename,
                    'res_model': 'gedoc.document',
                    'res_id': gedoc_document_id,
                })
                gedoc_document_obj.write(cr, uid, [gedoc_document_id], {'main_doc_id': main_doc_id})

            else:
                errore = 'Errore nel caricamento del file'
                esito = False

        except Exception as exception:
            esito = False
            errore = exception

        return esito, errore, gedoc_document_id

    def _process_protocol(self, cr, uid, conn, importer, file_to_import, storico_importer_id, doc_counter):
        esito = True
        errore = ''
        protocollo_id = None

        if importer.tipologia_importazione == 'aggancio':
            esito, errore, protocollo_id = self._process_protocol_aggancio(cr, uid, conn, importer, file_to_import,
                                                                           storico_importer_id)
        elif importer.tipologia_importazione == 'creazione':
            esito, errore, protocollo_id = self._process_protocol_creazione(cr, uid, conn, importer, file_to_import,
                                                                            storico_importer_id, doc_counter)

        return esito, errore, protocollo_id

    def _process_document(self, cr, uid, conn, importer, file_to_import, today_processed_folder_path, today_folder,
                          storico_importer_id, doc_counter):
        storico_importer_file_obj = self.pool.get('dematerializzazione.storico.importazione.importer.file')

        esito = False
        storico_importer_file_id = storico_importer_file_obj.create(cr, uid, {
            'name': file_to_import.filename,
            'storico_importazione_importer_id': storico_importer_id,
        })

        ext = file_to_import.filename.split('.')[-1]
        if ext != 'pdf' and ext != 'PDF':
            self._log_file_error(cr, uid, conn, importer, file_to_import, today_folder, storico_importer_file_id,
                                 'Il file deve avere estensione pdf')
            return esito

        try:
            esito, errore, protocollo_id = self._process_protocol(cr, uid, conn, importer, file_to_import,
                                                                  storico_importer_id, doc_counter)

            if esito:
                old_path = os.path.join(importer.path, file_to_import.filename)
                new_path = os.path.join(today_processed_folder_path, file_to_import.filename)
                conn.rename(importer.share, old_path, new_path)

                esito_valori = {
                    'esito': 'ok'
                }

                if protocollo_id:
                    if importer.tipologia_importazione == 'aggancio':
                        esito_valori['protocollo_id'] = protocollo_id
                    else:
                        esito_valori['document_id'] = protocollo_id

                storico_importer_file_obj.write(cr, uid, [storico_importer_file_id], esito_valori)
            else:
                self._log_file_error(cr, uid, conn, importer, file_to_import, today_folder, storico_importer_file_id,
                                     errore)

        except Exception as exception:
            self._log_file_error(cr, uid, conn, importer, file_to_import, today_folder, storico_importer_file_id,
                                 exception)

        return esito

    def importa_documenti_cron(self, cr, uid, automatic=False, context=None):
        self.importa_documenti(cr, uid, 'cron')

    def importa_documenti(self, cr, uid, modalita):
        storico_obj = self.pool.get('dematerializzazione.storico.importazione')
        importer_obj = self.pool.get('dematerializzazione.importer')
        storico_importer_obj = self.pool.get('dematerializzazione.storico.importazione.importer')

        esito = True
        dest_tz = pytz.timezone("Europe/Rome")
        now = datetime.datetime.now(dest_tz)
        today_folder = "%s_%d" % (now.strftime("%Y%m%d%H%M%S"), uid)

        storico_id = storico_obj.create(cr, uid, {
            'inizio': now,
            'modalita': modalita,
            'esito': 'errore'
        })

        # configurazione_ids = self.pool.get('dematerializzazione.configurazione').search(cr, uid, [])
        # configurazione = self.pool.get('dematerializzazione.configurazione').browse(cr, uid, configurazione_ids[0])

        importer_ids = importer_obj.search(cr, uid, [])
        importers = importer_obj.browse(cr, uid, importer_ids)

        for importer in importers:

            if importer.import_attivo:
                esito_importer = True
                conn = None

                smb_domain = importer.auth and importer.smb_domain or ''
                user = importer.auth and importer.user or ''
                password = importer.auth and importer.password or ''
                local_name = importer.title.encode('ascii', 'ignore')
                remote_name = importer.address.encode('ascii', 'ignore')

                try:
                    storico_importer_id = storico_importer_obj.create(cr, uid, {
                        'name': importer.title,
                        'indirizzo': importer.address,
                        'porta': 0,
                        'cartella': importer.share,
                        'esito': 'ok',
                        'storico_importazione_id': storico_id,
                    })

                    conn = SMBConnection(
                        domain=smb_domain,
                        username=user,
                        password=password,
                        my_name=local_name,
                        remote_name=remote_name
                    )
                    conn.connect(remote_name)

                    processed_folder_path = os.path.join(importer.path, importer.processed_folder)
                    today_processed_folder_path = os.path.join(processed_folder_path, today_folder)

                    self._directory_check(conn, importer.share, processed_folder_path, today_folder)

                    files_to_import = conn.listPath(importer.share, importer.path)

                    esito_documents = True
                    notempty_documents = True
                    doc_counter = 1
                    for file_to_import in files_to_import:
                        if not file_to_import.isDirectory:
                            esito_document = self._process_document(cr, uid, conn, importer,
                                                                    file_to_import,
                                                                    today_processed_folder_path,
                                                                    today_folder,
                                                                    storico_importer_id,
                                                                    doc_counter)
                            doc_counter = doc_counter + 1
                            esito_documents = esito_documents and esito_document

                    if doc_counter == 1:
                        storico_importer_obj.write(cr, uid, [storico_importer_id], {
                            'esito': 'errore',
                            'errore': 'Nessun file presente nella cartella',
                        })
                        esito_importer = False
                        notempty_documents = False

                    if not esito_documents:
                        storico_importer_obj.write(cr, uid, [storico_importer_id], {
                            'esito': 'errore',
                            'errore': 'Errore nel processamento dei file',
                        })
                        esito_importer = False

                    esito_importer = esito_documents and notempty_documents

                except Exception as exception:
                    esito_importer = False
                    storico_importer_obj.write(cr, uid, [storico_importer_id], {
                        'esito': 'errore',
                        'errore': exception
                    })
                finally:
                    if conn:
                        conn.close()
                    esito = esito and esito_importer

        storico_obj.write(cr, uid, [storico_id], {'fine': datetime.datetime.now()})

        if esito:
            storico_obj.write(cr, uid, [storico_id], {'esito': 'ok'})

        return storico_id

    def action_verifica_connessione(self, cr, uid, ids, context=None):
        rec = self.browse(cr, uid, ids[0], context=context)

        if rec.import_attivo:
            conn = None

            smb_domain = rec.auth and rec.smb_domain or ''
            user = rec.auth and rec.user or ''
            password = rec.auth and rec.password or ''
            local_name = rec.title.encode('ascii', 'ignore')
            remote_name = rec.address.encode('ascii', 'ignore')

            try:
                conn = SMBConnection(
                    domain=smb_domain,
                    username=user,
                    password=password,
                    my_name=local_name,
                    remote_name=remote_name
                )
                esito_test = conn.connect(remote_name)
                path = "/"
                self._directory_check(conn, rec.share, path, rec.processed_folder)
            except Exception as exception:
                esito_test = False

            if esito_test:
                self.write(cr, uid, ids[0], {'test_connessione': 'Connesso'}, context)
                conn.close()
            else:
                self.write(cr, uid, ids[0], {'test_connessione': 'Fallito'}, context)
                conn.close()
