from collections import defaultdict 
from importlib import import_module
from courseware.models import StudentModuleCache
from courseware.module_render import get_module
from xmodule.modulestore import Location
from xmodule.modulestore.django import modulestore
from django.http import HttpResponse
from django.utils import simplejson
from django.db import connection
from django.conf import settings
from django.core.urlresolvers import reverse
from django_comment_client.permissions import check_permissions_by_view
from mitxmako import middleware

import logging
import operator
import itertools
import urllib
import pystache_custom as pystache


# TODO these should be cached via django's caching rather than in-memory globals
_FULLMODULES = None
_DISCUSSIONINFO = None


def extract(dic, keys):
    return {k: dic.get(k) for k in keys}

def strip_none(dic):
    return dict([(k, v) for k, v in dic.iteritems() if v is not None])

def strip_blank(dic):
    def _is_blank(v):
        return isinstance(v, str) and len(v.strip()) == 0
    return dict([(k, v) for k, v in dic.iteritems() if not _is_blank(v)])

def merge_dict(dic1, dic2):
    return dict(dic1.items() + dic2.items())

def get_full_modules():
    global _FULLMODULES
    if not _FULLMODULES:
        _FULLMODULES = modulestore().modules
    return _FULLMODULES

def get_discussion_id_map(course):
    """
        return a dict of the form {category: modules}
    """
    global _DISCUSSIONINFO
    if not _DISCUSSIONINFO:
        initialize_discussion_info(course)
    return _DISCUSSIONINFO['id_map']

def get_discussion_title(request, course, discussion_id):
    global _DISCUSSIONINFO
    if not _DISCUSSIONINFO:
        initialize_discussion_info(course)
    title = _DISCUSSIONINFO['id_map'].get(discussion_id, {}).get('title', '(no title)')
    return title

def get_discussion_category_map(course):

    global _DISCUSSIONINFO
    if not _DISCUSSIONINFO:
        initialize_discussion_info(course)
    return _DISCUSSIONINFO['category_map']

def sort_map_entries(category_map):
    things = []
    for title, entry in category_map["entries"].items():
        things.append((title, entry))
    for title, category in category_map["subcategories"].items():
        things.append((title, category))
        sort_map_entries(category_map["subcategories"][title])
    category_map["children"] = [x[0] for x in sorted(things, key=lambda x: x[1]["sort_key"])]


def initialize_discussion_info(course):

    global _DISCUSSIONINFO
    if _DISCUSSIONINFO:
        return

    course_id = course.id
    url_course_id = course_id.replace('/', '_').replace('.', '_')

    all_modules = get_full_modules()[course_id]

    discussion_id_map = {}
    unexpanded_category_map = defaultdict(list)
    for location, module in all_modules.items():
        if location.category == 'discussion':
            id = module.metadata['id']
            category = module.metadata['discussion_category']
            title = module.metadata['for']
            sort_key = module.metadata.get('sort_key', title)
            discussion_id_map[id] = {"location": location, "title": title}
            category = " / ".join([x.strip() for x in category.split("/")])
            unexpanded_category_map[category].append({"title": title, "id": id,
                "sort_key": sort_key})

    category_map = {"entries": defaultdict(dict), "subcategories": defaultdict(dict)} 
    for category_path, entries in unexpanded_category_map.items():
        node = category_map["subcategories"]
        path = [x.strip() for x in category_path.split("/")]
        for level in path[:-1]:
            if level not in node:
                node[level] = {"subcategories": defaultdict(dict), 
                               "entries": defaultdict(dict),
                               "sort_key": level} 
            node = node[level]["subcategories"]

        level = path[-1]
        if level not in node:
            node[level] = {"subcategories": defaultdict(dict), 
                            "entries": defaultdict(dict), 
                            "sort_key": level}
        for entry in entries:
            node[level]["entries"][entry["title"]] = {"id": entry["id"], 
                                                      "sort_key": entry["sort_key"]}

    sort_map_entries(category_map)

    _DISCUSSIONINFO = {}

    _DISCUSSIONINFO['id_map'] = discussion_id_map

    _DISCUSSIONINFO['category_map'] = category_map

class JsonResponse(HttpResponse):
    def __init__(self, data=None):
        content = simplejson.dumps(data)
        super(JsonResponse, self).__init__(content,
                                           mimetype='application/json; charset=utf8')

class JsonError(HttpResponse):
    def __init__(self, error_messages=[]):
        if isinstance(error_messages, str):
            error_messages = [error_messages]
        content = simplejson.dumps({'errors': error_messages},
                                   indent=2,
                                   ensure_ascii=False)
        super(JsonError, self).__init__(content,
                                        mimetype='application/json; charset=utf8', status=400)

class HtmlResponse(HttpResponse):
    def __init__(self, html=''):
        super(HtmlResponse, self).__init__(html, content_type='text/plain')

class ViewNameMiddleware(object):
    def process_view(self, request, view_func, view_args, view_kwargs):
        request.view_name = view_func.__name__

class QueryCountDebugMiddleware(object):
    """
    This middleware will log the number of queries run
    and the total time taken for each request (with a
    status code of 200). It does not currently support
    multi-db setups.
    """
    def process_response(self, request, response):
        if response.status_code == 200:
            total_time = 0

            for query in connection.queries:
                query_time = query.get('time')
                if query_time is None:
                    # django-debug-toolbar monkeypatches the connection
                    # cursor wrapper and adds extra information in each
                    # item in connection.queries. The query time is stored
                    # under the key "duration" rather than "time" and is
                    # in milliseconds, not seconds.
                    query_time = query.get('duration', 0) / 1000
                total_time += float(query_time)

            logging.info('%s queries run, total %s seconds' % (len(connection.queries), total_time))
        return response

def get_ability(course_id, content, user):
    return {
            'editable': check_permissions_by_view(user, course_id, content, "update_thread" if content['type'] == 'thread' else "update_comment"),
            'can_reply': check_permissions_by_view(user, course_id, content, "create_comment" if content['type'] == 'thread' else "create_sub_comment"),
            'can_endorse': check_permissions_by_view(user, course_id, content, "endorse_comment") if content['type'] == 'comment' else False,
            'can_delete': check_permissions_by_view(user, course_id, content, "delete_thread" if content['type'] == 'thread' else "delete_comment"),
            'can_openclose': check_permissions_by_view(user, course_id, content, "openclose_thread") if content['type'] == 'thread' else False,
            'can_vote': check_permissions_by_view(user, course_id, content, "vote_for_thread" if content['type'] == 'thread' else "vote_for_comment"),
    }

def get_annotated_content_info(course_id, content, user, user_info):
    voted = ''
    if content['id'] in user_info['upvoted_ids']:
        voted = 'up'
    elif content['id'] in user_info['downvoted_ids']:
        voted = 'down'
    return {
        'voted': voted,
        'subscribed': content['id'] in user_info['subscribed_thread_ids'],
        'ability': get_ability(course_id, content, user),
    }

def get_annotated_content_infos(course_id, thread, user, user_info):
    infos = {}
    def annotate(content):
        infos[str(content['id'])] = get_annotated_content_info(course_id, content, user, user_info)
        for child in content.get('children', []):
            annotate(child)
    annotate(thread)
    return infos

# put this method in utils.py to avoid circular import dependency between helpers and mustache_helpers
def url_for_tags(course_id, tags):
    return reverse('django_comment_client.forum.views.forum_form_discussion', args=[course_id]) + '?' + urllib.urlencode({'tags': tags})

def render_mustache(template_name, dictionary, *args, **kwargs):
    template = middleware.lookup['main'].get_template(template_name).source
    return pystache.render(template, dictionary)

def permalink(content):
    if content['type'] == 'thread':
        return reverse('django_comment_client.forum.views.single_thread',
                       args=[content['course_id'], content['commentable_id'], content['id']])
    else:
        return reverse('django_comment_client.forum.views.single_thread',
                       args=[content['course_id'], content['commentable_id'], content['thread_id']]) + '#' + content['id']

def extend_content(content):
    content_info = {
        'displayed_title': content.get('highlighted_title') or content.get('title', ''),
        'displayed_body': content.get('highlighted_body') or content.get('body', ''),
        'raw_tags': ','.join(content.get('tags', [])),
        'permalink': permalink(content),
    }
    return merge_dict(content, content_info)

def get_courseware_context(content, course):
    id_map = get_discussion_id_map(course)
    id = content['commentable_id'] 
    content_info = None
    if id in id_map:
        location = id_map[id]["location"].url()
        title = id_map[id]["title"]
        content_info = { "courseware_location": location, "courseware_title": title}
    return content_info


def safe_content(content):
    fields = [
        'id', 'title', 'body', 'course_id', 'anonymous', 'endorsed',
        'parent_id', 'thread_id', 'votes', 'closed',
        'created_at', 'updated_at', 'depth', 'type',
        'commentable_id', 'comments_count', 'at_position_list',
        'children', 'highlighted_title', 'highlighted_body',
        'marked_title', 'marked_body',
    ]

    if content.get('anonymous') is False:
        fields += ['username', 'user_id']

    return strip_none(extract(content, fields))
