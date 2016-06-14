#!/usr/bin/env python
# -*- coding: utf-8 -*-

###############################################################################
#  Copyright 2016 Kitware Inc.
#
#  Licensed under the Apache License, Version 2.0 ( the "License" );
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.
###############################################################################

from girder.api import access
from girder.api.describe import Description, describeRoute
from girder.api.rest import Resource, loadmodel
from girder.constants import AccessType
from girder.utility import mail_utils
import datetime
import posixpath


CURATION = 'curation'

ENABLED = 'enabled'
STATUS = 'status'
TIMELINE = 'timeline'
ENABLE_USER_ID = 'enableUserId'
REQUEST_USER_ID = 'requestUserId'
REVIEW_USER_ID = 'reviewUserId'

CONSTRUCTION = 'construction'
REQUESTED = 'requested'
APPROVED = 'approved'

DEFAULTS = {
    ENABLED: False,
    STATUS: CONSTRUCTION,
}


class CuratedFolder(Resource):

    @access.public
    @loadmodel(model='folder', level=AccessType.READ)
    @describeRoute(
        Description('Get curation details for the folder.')
        .param('id', 'The folder ID', paramType='path')
        .errorResponse('ID was invalid.')
        .errorResponse('Read permission denied on the folder.', 403)
    )
    def getCuration(self, folder, params):
        result = dict(DEFAULTS)
        result[TIMELINE] = []
        result.update(folder.get(CURATION, {}))
        return result

    @access.public
    @loadmodel(model='folder', level=AccessType.READ)
    @describeRoute(
        Description('Set curation details for the folder.')
        .param('id', 'The folder ID', paramType='path')
        .param('enabled', 'Enable or disable folder curation.',
               required=False, dataType='boolean')
        .param('status', 'Set the folder curation status.',
               required=False, dataType='string')
        .errorResponse('ID was invalid.')
        .errorResponse('Write permission denied on the folder.', 403)
    )
    def setCuration(self, folder, params):
        user = self.getCurrentUser()
        if CURATION not in folder:
            folder[CURATION] = dict(DEFAULTS)
        curation = folder[CURATION]
        oldCuration = dict(curation)

        # update enabled
        oldEnabled = curation.get(ENABLED, False)
        if ENABLED in params:
            self.requireAdmin(user)
            curation[ENABLED] = self.boolParam(ENABLED, params)
        enabled = curation.get(ENABLED, False)

        # update status
        oldStatus = curation.get(STATUS)
        if STATUS in params:
            # regular users can only do construction -> requested transition
            if curation.get(STATUS) != CONSTRUCTION or \
               params[STATUS] != REQUESTED:
                self.requireAdmin(user)
            curation[STATUS] = params[STATUS]
        status = curation.get(STATUS)

        # admin enabling curation
        if enabled and not oldEnabled:
            # TODO: check if writers exist?
            # TODO: email writers?
            folder['public'] = False
            curation[ENABLE_USER_ID] = user.get('_id')
            self._addTimeline(oldCuration, curation, 'enabled curation')

        # admin disabling curation
        if not enabled and oldEnabled:
            self._addTimeline(oldCuration, curation, 'disabled curation')

        # user requesting approval
        if enabled and oldStatus == CONSTRUCTION and status == REQUESTED:
            curation[REQUEST_USER_ID] = user.get('_id')
            self._makeReadOnly(folder)
            self._addTimeline(oldCuration, curation, 'requested approval')
            # send email to admin requesting approval
            self._sendMail(
                folder, curation.get(ENABLE_USER_ID),
                'REQUEST FOR APPROVAL: ' + folder['name'],
                'curation.requested.mako')

        # admin approving request
        if enabled and oldStatus == REQUESTED and status == APPROVED:
            folder['public'] = True
            curation[REVIEW_USER_ID] = user.get('_id')
            self._addTimeline(oldCuration, curation, 'approved request')
            # send approval notification to requestor
            self._sendMail(
                folder, curation.get(REQUEST_USER_ID),
                'APPROVED: ' + folder['name'],
                'curation.approved.mako')

        # admin rejecting request
        if enabled and oldStatus == REQUESTED and status == CONSTRUCTION:
            curation[REVIEW_USER_ID] = user.get('_id')
            self._makeWriteable(folder)
            self._addTimeline(oldCuration, curation, 'rejected request')
            # send rejection notification to requestor
            self._sendMail(
                folder, curation.get(REQUEST_USER_ID),
                'REJECTED: ' + folder['name'],
                'curation.rejected.mako')

        # admin reopening folder
        if enabled and oldStatus == APPROVED and status == CONSTRUCTION:
            folder['public'] = False
            self._makeWriteable(folder)
            self._addTimeline(oldCuration, curation, 'reopened folder')

        self.model('folder').save(folder)
        return curation

    def _makeReadOnly(self, folder):
        for doc in folder['access']['users'] + folder['access']['groups']:
            if doc['level'] == AccessType.WRITE:
                doc['level'] = AccessType.READ

    def _makeWriteable(self, folder):
        for doc in folder['access']['users'] + folder['access']['groups']:
            if doc['level'] == AccessType.READ:
                doc['level'] = AccessType.WRITE

    def _addTimeline(self, oldCuration, curation, text):
        user = self.getCurrentUser()
        data = dict(
            userId=user.get('_id'),
            userLogin=user.get('login'),
            text=text,
            oldEnabled=oldCuration[ENABLED],
            oldStatus=oldCuration[STATUS],
            enabled=curation[ENABLED],
            status=curation[STATUS],
            timestamp=datetime.datetime.utcnow())
        curation.setdefault(TIMELINE, []).append(data)

    def _getEmail(self, userId):
        return self.model('user').load(userId, force=True).get('email')

    def _sendMail(self, folder, userId, subject, template):
        if not userId:
            return
        data = dict(
            folder=folder,
            curation=folder[CURATION],
            host=posixpath.dirname(mail_utils.getEmailUrlPrefix()))
        text = mail_utils.renderTemplate(template, data)
        emails = [self._getEmail(userId)]
        mail_utils.sendEmail(emails, subject, text)


def load(info):
    curatedFolder = CuratedFolder()
    info['apiRoot'].folder.route(
        'GET', (':id', 'curation'), curatedFolder.getCuration)
    info['apiRoot'].folder.route(
        'PUT', (':id', 'curation'), curatedFolder.setCuration)
