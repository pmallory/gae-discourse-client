"""Gateway for accessing the Discourse API (for forums)"""

import json
import re
from urllib import urlencode

from google.appengine.api import urlfetch
from google.appengine.ext import ndb


class Error(Exception):
    pass


class DiscourseAPIClient(object):
    """An API client for interacting with Discourse"""

    def __init__(self, discourse_url, api_key, api_username='system'):
        self._discourse_url = discourse_url
        self._api_key = api_key
        self._api_username = api_username

    @ndb.tasklet
    def _getRequest(self, req_string, params=None, payload=None):
        response = yield self._sendDiscourseRequest(req_string, params,
                                                    payload, 'GET')
        raise ndb.Return(response)

    @ndb.tasklet
    def _putRequest(self, req_string, params=None, payload=None):
        response = yield self._sendDiscourseRequest(req_string, params,
                                                    payload, 'PUT')
        raise ndb.Return(response)

    @ndb.tasklet
    def _postRequest(self, req_string, params=None, payload=None):
        response = yield self._sendDiscourseRequest(req_string, params,
                                                    payload, 'POST')
        raise ndb.Return(response)

    @ndb.tasklet
    def _deleteRequest(self, req_string, params=None, payload=None):
        response = yield self._sendDiscourseRequest(req_string, params,
                                                    payload, 'DELETE')
        raise ndb.Return(response)

    @ndb.tasklet
    def _sendDiscourseRequest(self, req_string, params, payload, method):
        if payload is None:
            payload = {}
        if params is None:
            params = {}

        if method == 'GET':
            params.update({'api_key': self._api_key,
                           'api_username': self._api_username})
        else:
            payload.update({'api_key': self._api_key,
                            'api_username': self._api_username})

        if params:
            url = '%s%s?%s' % (self._discourse_url, req_string,
                               urlencode(params))
        else:
            url = '%s%s' % (self._discourse_url, req_string)

        response = yield ndb.get_context().urlfetch(
            url=url, payload=urlencode(payload), method=method,
            headers={'Content-Type': 'application/x-www-form-urlencoded'}
        )
        if response.status_code != 200:
            raise Error("Request returned a code of %d: %s" %
                        (response.status_code, response.content))

        raise ndb.Return(json.loads(response.content))

    # USER ACTIONS

    @ndb.tasklet
    def getUserByEmail(self, user_email):
        """Finds a user with the given email

        This method takes a user email and returns a future which resolves to
        the Discourse user with that email address, if they exist. If no user
        is found, None is returned.
        """
        users = yield self._getRequest('admin/users/list/active.json',
                                       params={'filter': user_email,
                                               'show_emails': 'true'})

        for user in users:
            if user['email'].lower() == user_email.lower():
                raise ndb.Return(user['username'])

        raise ndb.Return(None)

    @ndb.tasklet
    def createUser(self, name, email, password, username, external_id=None):
        """Create a Discourse account

        This method takes a user object and returns the Discourse API response
        containing the user information for that user. If there is already a
        Discourse user with the given email address, that user will be
        returned.
        """

        user = yield self.getUserByEmail(email)
        if user:
            raise ndb.Return(user)

        payload = {
            'username': username,
            'email': email,
            'name': name,
            'password': password,
        }

        if external_id:
            payload['external_id'] = external_id

        response = yield self._postRequest('users/', payload=payload)
        raise ndb.Return(response)

    # CATEGORY ACTIONS

    @ndb.tasklet
    def getCategoryByName(self, category_name):
        categories = yield self._getRequest('categories.json')

        for category in categories['category_list']['categories']:
            if category['name'] == category_name:
                raise ndb.Return(category)

        raise ndb.Return(None)

    @ndb.tasklet
    def createCategory(self, category_name, slug, parent_category_name=None,
                       **kwargs):
        """Create a category"""

        payload = {
            'name': category_name,
            'slug': slug,
            'color': color,
            'text_color': text_color,
            'allow_badges': True
        }

        for k, v in kwargs:
            payload[k] = v

        if parent_category_name:
            parent_category = yield \
                self.getCategoryByName(parent_category_name)
            payload['parent_category_id'] = parent_category['id']

        response = yield self._postRequest('categories', payload=payload)
        raise ndb.Return(response)

    # GROUP ACTIONS

    @ndb.tasklet
    def addUserToGroup(self, user_email, group_name):
        """Adds the given account to the Discourse group with the given name"""

        user = yield self.getUserByEmail(user_email)
        if not user:
            raise Error("Unable to find user with email %s" % user_email)

        groups = yield self._getRequest('admin/groups.json')

        group_id = None
        for group in groups:
            if group['name'] == group_name:
                group_id = group['id']
                break
        else:
            raise Error("Group named %s not found" % group_name)

        payload = {
            'usernames': username
        }

        result = yield self._putRequest('admin/groups/%s/members.json' %
                                        group_id, payload=payload)
        raise ndb.Return(result)

    @ndb.tasklet
    def removeUserFromGroup(self, user_email, group_name):
        """Removes an account from a group"""

        user_id = yield self.getUserIdByEmail(user_email)
        if not user_id:
            raise Error("Unable to find user with email %s" % user_email)

        groups = yield self._getRequest('admin/groups.json')

        group_id = None
        for group in groups:
            if group['name'] == group_name:
                group_id = group['id']
                break
        else:
            raise Error("Group named %s not found" % group_name)

        result = yield self._deleteRequest('admin/users/%s/groups/%s' %
                                           (user_id, group_id))
        raise ndb.Return(result)

    @ndb.tasklet
    def createGroup(self, group_name, **kwargs):
        """Creates a group with the given name on Discourse"""

        groups = yield self._getRequest('admin/groups.json')

        group_id = None
        for group in groups:
            if group['name'] == group_name:
                raise Error("Group named %s already exists!" % group_name)

        payload = {
            'name': group_name
        }

        for k, v in kwargs:
            payload[k] = v

        response = yield self._postRequest('admin/groups', payload=payload)
        raise ndb.Return(response)

    # CONTENT ACTIONS

    @ndb.tasklet
    def createPost(self, text, title, category_name, **kwargs):
        """Creates a post"""

        category = yield self.getCategoryByName(category_name)

        payload = {
            'raw': text,
            'title': title,
            'category': category['id']
        }

        for k, v in kwargs:
            payload[k] = v

        response = yield self._postRequest('posts', payload=payload)
