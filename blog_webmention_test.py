"""Unit tests for blog_webmention.py.
"""

__author__ = ['Ryan Barrett <bridgy@ryanb.org>']

import json
import mox
import urllib

import appengine_config
from appengine_config import HTTP_TIMEOUT

from models import BlogWebmention, Publish, PublishedPage
import blog_webmention
import testutil

from google.appengine.api import mail


class BlogWebmentionTest(testutil.HandlerTest):

  def setUp(self):
    super(BlogWebmentionTest, self).setUp()
    blog_webmention.SOURCES['fake'] = testutil.FakeSource
    self.source = testutil.FakeSource(id='foo.com', domain='foo.com',
                                      features=['webmention'])
    self.source.put()

    self.mox.StubOutWithMock(testutil.FakeSource, 'create_comment')#, use_mock_anything=True)

  def get_response(self, source=None, target=None):
    if source is None:
      source = 'http://bar.com/reply'
    if target is None:
      target = 'http://foo.com/post/1'
    return blog_webmention.application.get_response(
      '/webmention/fake', method='POST',
      body='source=%s&target=%s' % (source, target))

  def assert_error(self, expected_error, status=400, **kwargs):
    resp = self.get_response(**kwargs)
    self.assertEquals(status, resp.status_int)
    self.assertIn(expected_error, json.loads(resp.body)['error'])

  def test_success(self):
    html = """
<article class="h-entry">
<p class="p-author">my name</p>
<p class="e-content">
i hereby reply
<a class="u-in-reply-to" href="http://foo.com/post/1"></a>
</p></article>"""
    self.expect_requests_get('http://bar.com/reply', html)

    testutil.FakeSource.create_comment(
      'http://foo.com/post/1', 'my name', 'http://foo.com/',
      'i hereby reply\n<a class="u-in-reply-to" href="http://foo.com/post/1"></a>')
    self.mox.ReplayAll()

    resp = self.get_response()
    self.assertEquals(200, resp.status_int, resp.body)
    # TODO
    # self.assertEquals('{"id": "..."}', json.loads(resp.body))

    entity = BlogWebmention.get_by_id('http://bar.com/reply')
    self.assertEquals(self.source.key, entity.source)
    self.assertEquals('complete', entity.status)
    self.assertEquals('comment', entity.type)
    self.assertEquals(html, entity.html)
    # self.assertEquals({'id': 'fake id', 'url': 'http://fake/url',
    #                    'content': 'foo - http://foo.com/'},
    #                   publish.published)

  def test_domain_not_found(self):
    # no source
    msg = 'Could not find FakeSource account for foo.com.'
    self.source.key.delete()
    self.assert_error(msg)
    self.assertEquals(0, BlogWebmention.query().count())

    # source without webmention feature
    self.source.features = ['listen']
    self.source.put()
    self.assert_error(msg)
    self.assertEquals(0, BlogWebmention.query().count())

  def test_domain_translates_to_lowercase(self):
    html = '<article class="h-entry"><p class="e-content">X http://FoO.cOm/post/1</p></article>'
    self.expect_requests_get('http://bar.com/reply', html)

    testutil.FakeSource.create_comment(
      'http://FoO.cOm/post/1', 'foo.com', 'http://foo.com/', 'X http://FoO.cOm/post/1')
    self.mox.ReplayAll()

    resp = self.get_response(target='http://FoO.cOm/post/1')
    self.assertEquals(200, resp.status_int, resp.body)
    self.assertEquals('complete', BlogWebmention.get_by_id('http://bar.com/reply').status)

  def test_source_link_not_found(self):
    html = '<article class="h-entry"></article>'
    self.expect_requests_get('http://bar.com/reply', html)
    self.mox.ReplayAll()
    self.assert_error('Could not find target URL')
    self.assertEquals('failed', BlogWebmention.get_by_id('http://bar.com/reply').status)

  def test_source_missing_mf2(self):
    self.expect_requests_get('http://bar.com/reply', '')
    self.mox.ReplayAll()
    self.assert_error('No microformats2 data found')
    self.assertEquals('failed', BlogWebmention.get_by_id('http://bar.com/reply').status)

  def test_repeated(self):
    # 1) first a failure
    self.expect_requests_get('http://bar.com/reply', '')

    # 2) should allow retrying, this one will succeed
    self.expect_requests_get('http://bar.com/reply', """
<article class="h-entry"><a class="u-repost-of" href="http://foo.com/post/1"></a></article>""")
    testutil.FakeSource.create_comment('http://foo.com/post/1', 'foo.com',
                                       'http://foo.com/', 'reposted this.')

    # 3) after success, another is a noop and returns 200
    # TODO: check for "updates not supported" message
    self.mox.ReplayAll()

    # now the webmention requests. 1) failure
    self.assert_error('No microformats2 data found')
    self.assertEquals('failed',
                      BlogWebmention.get_by_id('http://bar.com/reply').status)

    # 2) success
    resp = self.get_response()
    self.assertEquals(200, resp.status_int, resp.body)
    entity = BlogWebmention.get_by_id('http://bar.com/reply')
    self.assertEquals('complete', entity.status)
    self.assertEquals('repost', entity.type)

    # 3) noop repeated success
    # source without webmention feature
    resp = self.get_response()
    self.assertEquals(200, resp.status_int, resp.body)
    self.assertEquals('complete',
                      BlogWebmention.get_by_id('http://bar.com/reply').status)
