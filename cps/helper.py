#!/usr/bin/env python
# -*- coding: utf-8 -*-

#  This file is part of the Calibre-Web (https://github.com/janeczku/calibre-web)
#    Copyright (C) 2012-2019 cervinko, idalin, SiphonSquirrel, ouzklcn, akushsky,
#                            OzzieIsaacs, bodybybuddha, jkrehm, matthazinski, janeczku
#
#  This program is free software: you can redistribute it and/or modify
#  it under the terms of the GNU General Public License as published by
#  the Free Software Foundation, either version 3 of the License, or
#  (at your option) any later version.
#
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#  GNU General Public License for more details.
#
#  You should have received a copy of the GNU General Public License
#  along with this program. If not, see <http://www.gnu.org/licenses/>.


import db
import ub
from flask import current_app as app
# import logging
from tempfile import gettempdir
import sys
import os
import re
import unicodedata
# from io import BytesIO
import worker
import time
from flask import send_from_directory, make_response, redirect, abort
from flask_babel import gettext as _
from flask_login import current_user
from babel.dates import format_datetime
from datetime import datetime
# import threading
import shutil
import requests
# import zipfile
try:
    import gdriveutils as gd
except ImportError:
    pass
import web
# import server
import random
import subprocess

try:
    import unidecode
    use_unidecode = True
except ImportError:
    use_unidecode = False

# Global variables
# updater_thread = None
global_WorkerThread = worker.WorkerThread()
global_WorkerThread.start()


def update_download(book_id, user_id):
    check = ub.session.query(ub.Downloads).filter(ub.Downloads.user_id == user_id).filter(ub.Downloads.book_id ==
                                                                                          book_id).first()
    if not check:
        new_download = ub.Downloads(user_id=user_id, book_id=book_id)
        ub.session.add(new_download)
        ub.session.commit()

# Convert existing book entry to new format
def convert_book_format(book_id, calibrepath, old_book_format, new_book_format, user_id, kindle_mail=None):
    book = db.session.query(db.Books).filter(db.Books.id == book_id).first()
    data = db.session.query(db.Data).filter(db.Data.book == book.id).filter(db.Data.format == old_book_format).first()
    if not data:
        error_message = _(u"%(format)s format not found for book id: %(book)d", format=old_book_format, book=book_id)
        app.logger.error("convert_book_format: " + error_message)
        return error_message
    if ub.config.config_use_google_drive:
        df = gd.getFileFromEbooksFolder(book.path, data.name + "." + old_book_format.lower())
        if df:
            datafile = os.path.join(calibrepath, book.path, data.name + u"." + old_book_format.lower())
            if not os.path.exists(os.path.join(calibrepath, book.path)):
                os.makedirs(os.path.join(calibrepath, book.path))
            df.GetContentFile(datafile)
        else:
            error_message = _(u"%(format)s not found on Google Drive: %(fn)s",
                              format=old_book_format, fn=data.name + "." + old_book_format.lower())
            return error_message
    file_path = os.path.join(calibrepath, book.path, data.name)
    if os.path.exists(file_path + "." + old_book_format.lower()):
        # read settings and append converter task to queue
        if kindle_mail:
            settings = ub.get_mail_settings()
            settings['subject'] = _('Send to Kindle') # pretranslate Subject for e-mail
            settings['body'] = _(u'This e-mail has been sent via Calibre-Web.')
            # text = _(u"%(format)s: %(book)s", format=new_book_format, book=book.title)
        else:
            settings = dict()
        text = (u"%s -> %s: %s" % (old_book_format, new_book_format, book.title))
        settings['old_book_format'] = old_book_format
        settings['new_book_format'] = new_book_format
        global_WorkerThread.add_convert(file_path, book.id, user_id, text, settings, kindle_mail)
        return None
    else:
        error_message = _(u"%(format)s not found: %(fn)s",
                        format=old_book_format, fn=data.name + "." + old_book_format.lower())
        return error_message


def send_test_mail(kindle_mail, user_name):
    global_WorkerThread.add_email(_(u'Calibre-Web test e-mail'),None, None, ub.get_mail_settings(),
                                  kindle_mail, user_name, _(u"Test e-mail"),
                                  _(u'This e-mail has been sent via Calibre-Web.'))
    return


# Send registration email or password reset email, depending on parameter resend (False means welcome email)
def send_registration_mail(e_mail, user_name, default_password, resend=False):
    text = "Hello %s!\r\n" % user_name
    if not resend:
        text += "Your new account at Calibre-Web has been created. Thanks for joining us!\r\n"
    text += "Please log in to your account using the following informations:\r\n"
    text += "User name: %s\n" % user_name
    text += "Password: %s\r\n" % default_password
    text += "Don't forget to change your password after first login.\r\n"
    text += "Sincerely\r\n\r\n"
    text += "Your Calibre-Web team"
    global_WorkerThread.add_email(_(u'Get Started with Calibre-Web'),None, None, ub.get_mail_settings(),
                                  e_mail, user_name, _(u"Registration e-mail for user: %(name)s", name=user_name), text)
    return

def check_send_to_kindle(entry):
    """
        returns all available book formats for sending to Kindle
    """
    if len(entry.data):
        bookformats=list()
        if ub.config.config_ebookconverter == 0:
            # no converter - only for mobi and pdf formats
            for ele in iter(entry.data):
                if 'MOBI' in ele.format:
                    bookformats.append({'format':'Mobi','convert':0,'text':_('Send %(format)s to Kindle',format='Mobi')})
                if 'PDF' in ele.format:
                    bookformats.append({'format':'Pdf','convert':0,'text':_('Send %(format)s to Kindle',format='Pdf')})
                if 'AZW' in ele.format:
                    bookformats.append({'format':'Azw','convert':0,'text':_('Send %(format)s to Kindle',format='Azw')})
                if 'AZW3' in ele.format:
                    bookformats.append({'format':'Azw3','convert':0,'text':_('Send %(format)s to Kindle',format='Azw3')})
        else:
            formats = list()
            for ele in iter(entry.data):
                formats.append(ele.format)
            if 'MOBI' in formats:
                bookformats.append({'format': 'Mobi','convert':0,'text':_('Send %(format)s to Kindle',format='Mobi')})
            if 'AZW' in formats:
                bookformats.append({'format': 'Azw','convert':0,'text':_('Send %(format)s to Kindle',format='Azw')})
            if 'AZW3' in formats:
                bookformats.append({'format': 'Azw3','convert':0,'text':_('Send %(format)s to Kindle',format='Azw3')})
            if 'PDF' in formats:
                bookformats.append({'format': 'Pdf','convert':0,'text':_('Send %(format)s to Kindle',format='Pdf')})
            if ub.config.config_ebookconverter >= 1:
                if 'EPUB' in formats and not 'MOBI' in formats:
                    bookformats.append({'format': 'Mobi','convert':1,
                            'text':_('Convert %(orig)s to %(format)s and send to Kindle',orig='Epub',format='Mobi')})
            if ub.config.config_ebookconverter == 2:
                if 'EPUB' in formats and not 'AZW3' in formats:
                    bookformats.append({'format': 'Azw3','convert':1,
                            'text':_('Convert %(orig)s to %(format)s and send to Kindle',orig='Epub',format='Azw3')})
        return bookformats
    else:
        app.logger.error(u'Cannot find book entry %d', entry.id)
        return None


# Check if a reader is existing for any of the book formats, if not, return empty list, otherwise return
# list with supported formats
def check_read_formats(entry):
    EXTENSIONS_READER = {'TXT', 'PDF', 'EPUB', 'ZIP', 'CBZ', 'TAR', 'CBT', 'RAR', 'CBR'}
    bookformats = list()
    if len(entry.data):
        for ele in iter(entry.data):
            if ele.format in EXTENSIONS_READER:
                bookformats.append(ele.format.lower())
    return bookformats


# Files are processed in the following order/priority:
# 1: If Mobi file is existing, it's directly send to kindle email,
# 2: If Epub file is existing, it's converted and send to kindle email,
# 3: If Pdf file is existing, it's directly send to kindle email
def send_mail(book_id, book_format, convert, kindle_mail, calibrepath, user_id):
    """Send email with attachments"""
    book = db.session.query(db.Books).filter(db.Books.id == book_id).first()

    if convert:
        # returns None if success, otherwise errormessage
        return convert_book_format(book_id, calibrepath, u'epub', book_format.lower(), user_id, kindle_mail)
    else:
        for entry in iter(book.data):
            if entry.format.upper() == book_format.upper():
                result = entry.name + '.' + book_format.lower()
                global_WorkerThread.add_email(_(u"Send to Kindle"), book.path, result, ub.get_mail_settings(),
                                      kindle_mail, user_id, _(u"E-mail: %(book)s", book=book.title),
                                      _(u'This e-mail has been sent via Calibre-Web.'))
                return
        return _(u"The requested file could not be read. Maybe wrong permissions?")


def get_valid_filename(value, replace_whitespace=True):
    """
    Returns the given string converted to a string that can be used for a clean
    filename. Limits num characters to 128 max.
    """
    if value[-1:] == u'.':
        value = value[:-1]+u'_'
    value = value.replace("/", "_").replace(":", "_").strip('\0')
    if use_unidecode:
        value = (unidecode.unidecode(value)).strip()
    else:
        value = value.replace(u'§', u'SS')
        value = value.replace(u'ß', u'ss')
        value = unicodedata.normalize('NFKD', value)
        re_slugify = re.compile('[\W\s-]', re.UNICODE)
        if isinstance(value, str):  # Python3 str, Python2 unicode
            value = re_slugify.sub('', value).strip()
        else:
            value = unicode(re_slugify.sub('', value).strip())
    if replace_whitespace:
        #  *+:\"/<>? are replaced by _
        value = re.sub(r'[\*\+:\\\"/<>\?]+', u'_', value, flags=re.U)
        # pipe has to be replaced with comma
        value = re.sub(r'[\|]+', u',', value, flags=re.U)
    value = value[:128]
    if not value:
        raise ValueError("Filename cannot be empty")
    return value


def get_sorted_author(value):
    try:
        if ',' not in value:
            regexes = ["^(JR|SR)\.?$", "^I{1,3}\.?$", "^IV\.?$"]
            combined = "(" + ")|(".join(regexes) + ")"
            value = value.split(" ")
            if re.match(combined, value[-1].upper()):
                value2 = value[-2] + ", " + " ".join(value[:-2]) + " " + value[-1]
            elif len(value) == 1:
                value2 = value[0]
            else:
                value2 = value[-1] + ", " + " ".join(value[:-1])
        else:
            value2 = value
    except Exception:
        web.app.logger.error("Sorting author " + str(value) + "failed")
        value2 = value
    return value2


# Deletes a book fro the local filestorage, returns True if deleting is successfull, otherwise false
def delete_book_file(book, calibrepath, book_format=None):
    # check that path is 2 elements deep, check that target path has no subfolders
    if book.path.count('/') == 1:
        path = os.path.join(calibrepath, book.path)
        if book_format:
            for file in os.listdir(path):
                if file.upper().endswith("."+book_format):
                    os.remove(os.path.join(path, file))
        else:
            if os.path.isdir(path):
                if len(next(os.walk(path))[1]):
                    web.app.logger.error(
                        "Deleting book " + str(book.id) + " failed, path has subfolders: " + book.path)
                    return False
                shutil.rmtree(path, ignore_errors=True)
                return True
            else:
                web.app.logger.error("Deleting book " + str(book.id) + " failed, book path not valid: " + book.path)
                return False


def update_dir_structure_file(book_id, calibrepath, first_author):
    localbook = db.session.query(db.Books).filter(db.Books.id == book_id).first()
    path = os.path.join(calibrepath, localbook.path)

    authordir = localbook.path.split('/')[0]
    if first_author:
        new_authordir = get_valid_filename(first_author)
    else:
        new_authordir = get_valid_filename(localbook.authors[0].name)

    titledir = localbook.path.split('/')[1]
    new_titledir = get_valid_filename(localbook.title) + " (" + str(book_id) + ")"

    if titledir != new_titledir:
        try:
            new_title_path = os.path.join(os.path.dirname(path), new_titledir)
            if not os.path.exists(new_title_path):
                os.renames(path, new_title_path)
            else:
                web.app.logger.info("Copying title: " + path + " into existing: " + new_title_path)
                for dir_name, subdir_list, file_list in os.walk(path):
                    for file in file_list:
                        os.renames(os.path.join(dir_name, file),
                                   os.path.join(new_title_path + dir_name[len(path):], file))
            path = new_title_path
            localbook.path = localbook.path.split('/')[0] + '/' + new_titledir
        except OSError as ex:
            web.app.logger.error("Rename title from: " + path + " to " + new_title_path + ": " + str(ex))
            web.app.logger.debug(ex, exc_info=True)
            return _("Rename title from: '%(src)s' to '%(dest)s' failed with error: %(error)s",
                     src=path, dest=new_title_path, error=str(ex))
    if authordir != new_authordir:
        try:
            new_author_path = os.path.join(calibrepath, new_authordir, os.path.basename(path))
            os.renames(path, new_author_path)
            localbook.path = new_authordir + '/' + localbook.path.split('/')[1]
        except OSError as ex:
            web.app.logger.error("Rename author from: " + path + " to " + new_author_path + ": " + str(ex))
            web.app.logger.debug(ex, exc_info=True)
            return _("Rename author from: '%(src)s' to '%(dest)s' failed with error: %(error)s",
                     src=path, dest=new_author_path, error=str(ex))
    # Rename all files from old names to new names
    if authordir != new_authordir or titledir != new_titledir:
        try:
            for file_format in localbook.data:
                path_name = os.path.join(calibrepath, new_authordir, os.path.basename(path))
                new_name = get_valid_filename(localbook.title) + ' - ' + get_valid_filename(new_authordir)
                os.renames(os.path.join(path_name, file_format.name + '.' + file_format.format.lower()),
                           os.path.join(path_name,new_name + '.' + file_format.format.lower()))
                file_format.name = new_name
        except OSError as ex:
            web.app.logger.error("Rename file in path " + path + " to " + new_name + ": " + str(ex))
            web.app.logger.debug(ex, exc_info=True)
            return _("Rename file in path '%(src)s' to '%(dest)s' failed with error: %(error)s",
                     src=path, dest=new_name, error=str(ex))
    return False


def update_dir_structure_gdrive(book_id, first_author):
    error = False
    book = db.session.query(db.Books).filter(db.Books.id == book_id).first()

    authordir = book.path.split('/')[0]
    if first_author:
        new_authordir = get_valid_filename(first_author)
    else:
        new_authordir = get_valid_filename(book.authors[0].name)
    titledir = book.path.split('/')[1]
    new_titledir = get_valid_filename(book.title) + " (" + str(book_id) + ")"

    if titledir != new_titledir:
        gFile = gd.getFileFromEbooksFolder(os.path.dirname(book.path), titledir)
        if gFile:
            gFile['title'] = new_titledir

            gFile.Upload()
            book.path = book.path.split('/')[0] + '/' + new_titledir
            gd.updateDatabaseOnEdit(gFile['id'], book.path)     # only child folder affected
        else:
            error = _(u'File %(file)s not found on Google Drive', file=book.path) # file not found

    if authordir != new_authordir:
        gFile = gd.getFileFromEbooksFolder(os.path.dirname(book.path), titledir)
        if gFile:
            gd.moveGdriveFolderRemote(gFile,new_authordir)
            book.path = new_authordir + '/' + book.path.split('/')[1]
            gd.updateDatabaseOnEdit(gFile['id'], book.path)
        else:
            error = _(u'File %(file)s not found on Google Drive', file=authordir) # file not found
    # Rename all files from old names to new names
    # ToDo: Rename also all bookfiles with new author name and new title name
    '''
    if authordir != new_authordir or titledir != new_titledir:
        for format in book.data:
            # path_name = os.path.join(calibrepath, new_authordir, os.path.basename(path))
            new_name = get_valid_filename(book.title) + ' - ' + get_valid_filename(book)
            format.name = new_name
            if gFile:
                pass
            else:
                error = _(u'File %(file)s not found on Google Drive', file=format.name)  # file not found
                break'''
    return error


def delete_book_gdrive(book, book_format):
    error= False
    if book_format:
        name = ''
        for entry in book.data:
            if entry.format.upper() == book_format:
                name = entry.name + '.' + book_format
        gFile = gd.getFileFromEbooksFolder(book.path, name)
    else:
        gFile = gd.getFileFromEbooksFolder(os.path.dirname(book.path),book.path.split('/')[1])
    if gFile:
        gd.deleteDatabaseEntry(gFile['id'])
        gFile.Trash()
    else:
        error =_(u'Book path %(path)s not found on Google Drive', path=book.path)  # file not found
    return error


def generate_random_password():
    s = "abcdefghijklmnopqrstuvwxyz01234567890ABCDEFGHIJKLMNOPQRSTUVWXYZ!@#$%&*()?"
    passlen = 8
    return "".join(random.sample(s,passlen ))

################################## External interface

def update_dir_stucture(book_id, calibrepath, first_author = None):
    if ub.config.config_use_google_drive:
        return update_dir_structure_gdrive(book_id, first_author)
    else:
        return update_dir_structure_file(book_id, calibrepath, first_author)


def delete_book(book, calibrepath, book_format):
    if ub.config.config_use_google_drive:
        return delete_book_gdrive(book, book_format)
    else:
        return delete_book_file(book, calibrepath, book_format)


def get_book_cover(cover_path):
    if ub.config.config_use_google_drive:
        try:
            path=gd.get_cover_via_gdrive(cover_path)
            if path:
                return redirect(path)
            else:
                web.app.logger.error(cover_path + '/cover.jpg not found on Google Drive')
                return send_from_directory(os.path.join(os.path.dirname(__file__), "static"), "generic_cover.jpg")
        except Exception as e:
            web.app.logger.error("Error Message: "+e.message)
            web.app.logger.exception(e)
            # traceback.print_exc()
            return send_from_directory(os.path.join(os.path.dirname(__file__), "static"),"generic_cover.jpg")
    else:
        return send_from_directory(os.path.join(ub.config.config_calibre_dir, cover_path), "cover.jpg")


# saves book cover to gdrive or locally
def save_cover(url, book_path):
    img = requests.get(url)
    if img.headers.get('content-type') != 'image/jpeg':
        web.app.logger.error("Cover is no jpg file, can't save")
        return False

    if ub.config.config_use_google_drive:
        tmpDir = gettempdir()
        f = open(os.path.join(tmpDir, "uploaded_cover.jpg"), "wb")
        f.write(img.content)
        f.close()
        gd.uploadFileToEbooksFolder(os.path.join(book_path, 'cover.jpg'), os.path.join(tmpDir, f.name))
        web.app.logger.info("Cover is saved on Google Drive")
        return True

    f = open(os.path.join(ub.config.config_calibre_dir, book_path, "cover.jpg"), "wb")
    f.write(img.content)
    f.close()
    web.app.logger.info("Cover is saved")
    return True


def do_download_file(book, book_format, data, headers):
    if ub.config.config_use_google_drive:
        startTime = time.time()
        df = gd.getFileFromEbooksFolder(book.path, data.name + "." + book_format)
        web.app.logger.debug(time.time() - startTime)
        if df:
            return gd.do_gdrive_download(df, headers)
        else:
            abort(404)
    else:
        filename = os.path.join(ub.config.config_calibre_dir, book.path)
        if not os.path.isfile(os.path.join(filename, data.name + "." + book_format)):
            # ToDo: improve error handling
            web.app.logger.error('File not found: %s' % os.path.join(filename, data.name + "." + book_format))
        response = make_response(send_from_directory(filename, data.name + "." + book_format))
        response.headers = headers
        return response

##################################




def check_unrar(unrarLocation):
    error = False
    if os.path.exists(unrarLocation):
        try:
            if sys.version_info < (3, 0):
                unrarLocation = unrarLocation.encode(sys.getfilesystemencoding())
            p = subprocess.Popen(unrarLocation, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            p.wait()
            for lines in p.stdout.readlines():
                if isinstance(lines, bytes):
                    lines = lines.decode('utf-8')
                value=re.search('UNRAR (.*) freeware', lines)
                if value:
                    version = value.group(1)
        except OSError as e:
            error = True
            web.app.logger.exception(e)
            version =_(u'Error excecuting UnRar')
    else:
        version = _(u'Unrar binary file not found')
        error=True
    return (error, version)



def json_serial(obj):
    """JSON serializer for objects not serializable by default json code"""

    if isinstance(obj, (datetime)):
        return obj.isoformat()
    raise TypeError ("Type %s not serializable" % type(obj))


def render_task_status(tasklist):
    #helper function to apply localize status information in tasklist entries
    renderedtasklist=list()
    # task2 = task
    for task in tasklist:
        if task['user'] == current_user.nickname or current_user.role_admin():
            # task2 = copy.deepcopy(task) # = task
            if task['formStarttime']:
                task['starttime'] = format_datetime(task['formStarttime'], format='short', locale=web.get_locale())
            # task2['formStarttime'] = ""
            else:
                if 'starttime' not in task:
                    task['starttime'] = ""

            # localize the task status
            if isinstance( task['stat'], int ):
                if task['stat'] == worker.STAT_WAITING:
                    task['status'] = _(u'Waiting')
                elif task['stat'] == worker.STAT_FAIL:
                    task['status'] = _(u'Failed')
                elif task['stat'] == worker.STAT_STARTED:
                    task['status'] = _(u'Started')
                elif task['stat'] == worker.STAT_FINISH_SUCCESS:
                    task['status'] = _(u'Finished')
                else:
                    task['status'] = _(u'Unknown Status')

            # localize the task type
            if isinstance( task['taskType'], int ):
                if task['taskType'] == worker.TASK_EMAIL:
                    task['taskMessage'] = _(u'E-mail: ') + task['taskMess']
                elif  task['taskType'] == worker.TASK_CONVERT:
                    task['taskMessage'] = _(u'Convert: ') + task['taskMess']
                elif  task['taskType'] == worker.TASK_UPLOAD:
                    task['taskMessage'] = _(u'Upload: ') + task['taskMess']
                elif  task['taskType'] == worker.TASK_CONVERT_ANY:
                    task['taskMessage'] = _(u'Convert: ') + task['taskMess']
                else:
                    task['taskMessage'] = _(u'Unknown Task: ') + task['taskMess']

            renderedtasklist.append(task)

    return renderedtasklist
