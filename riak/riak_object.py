"""
Copyright 2012-2013 Basho Technologies <dev@basho.com>
Copyright 2010 Rusty Klophaus <rusty@basho.com>
Copyright 2010 Justin Sheehy <justin@basho.com>
Copyright 2009 Jay Baird <jay@mochimedia.com>

This file is provided to you under the Apache License,
Version 2.0 (the "License"); you may not use this file
except in compliance with the License.  You may obtain
a copy of the License at

  http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing,
software distributed under the License is distributed on an
"AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
KIND, either express or implied.  See the License for the
specific language governing permissions and limitations
under the License.
"""
from riak import ConflictError
from riak.content import RiakContent
from riak.util import deprecated
import base64


def content_property(name, doc=None):
    """
    Delegates a property to the first sibling in a RiakObject, raising
    an error when the object is in conflict.
    """
    def _setter(self, value):
        if len(self.siblings) == 0:
            # In this case, assume that what the user wants is to
            # create a new sibling inside an empty object.
            self.siblings = [RiakContent(self)]
        if len(self.siblings) != 1:
            raise ConflictError()
        setattr(self.siblings[0], name, value)

    def _getter(self):
        if len(self.siblings) == 0:
            return
        if len(self.siblings) != 1:
            raise ConflictError()
        return getattr(self.siblings[0], name)

    return property(_getter, _setter, doc=doc)


def content_method(name):
    """
    Delegates a method to the first sibling in a RiakObject, raising
    an error when the object is in conflict.
    """
    def _delegate(self, *args, **kwargs):
        if len(self.siblings) != 1:
            raise ConflictError()
        return getattr(self.siblings[0], name).__call__(*args, **kwargs)

    _delegate.__doc__ = getattr(RiakContent, name).__doc__

    return _delegate


class VClock(object):
    """
    A representation of a vector clock received from Riak.
    """

    _decoders = {
        'base64': base64.b64decode,
        'binary': str
    }

    _encoders = {
        'base64': base64.b64encode,
        'binary': str
    }

    def __init__(self, value, encoding):
        self._vclock = self._decoders[encoding].__call__(value)

    def encode(self, encoding):
        if encoding in self._encoders:
            return self._encoders[encoding].__call__(self._vclock)
        else:
            raise ValueError('{} is not a valid vector clock encoding'.
                             format(encoding))

    def __repr__(self):
        return '<{} {}>'.format(self.__class__.__name__,
                                self.encode('base64'))


class RiakObject(object):
    """
    The RiakObject holds meta information about a Riak object, plus the
    object's data.
    """
    def __init__(self, client, bucket, key=None):
        """
        Construct a new RiakObject.

        :param client: A RiakClient object.
        :type client: :class:`RiakClient <riak.client.RiakClient>`
        :param bucket: A RiakBucket object.
        :type bucket: :class:`RiakBucket <riak.bucket.RiakBucket>`
        :param key: An optional key. If not specified, then the key
         is generated by the server when :func:`store` is called.
        :type key: string
        """
        try:
            if isinstance(key, basestring):
                key = key.encode('ascii')
        except UnicodeError:
            raise TypeError('Unicode keys are not supported.')

        if key is not None and len(key) == 0:
            raise ValueError('Key name must either be "None"'
                             ' or a non-empty string.')

        self._resolver = None
        self.client = client
        self.bucket = bucket
        self.key = key
        self.vclock = None
        self.siblings = [RiakContent(self)]

    #: The list of sibling values contained in this object
    siblings = []

    def __hash__(self):
        return hash((self.key, self.bucket, self.vclock))

    def __eq__(self, other):
        if isinstance(other, self.__class__):
            return hash(self) == hash(other)
        else:
            return False

    def __ne__(self, other):
        if isinstance(other, self.__class__):
            return hash(self) != hash(other)
        else:
            return True

    data = content_property('data', doc="""
        The data stored in this object, as Python objects. For the raw
        data, use the `encoded_data` property. If unset, accessing
        this property will result in decoding the `encoded_data`
        property into Python values. The decoding is dependent on the
        `content_type` property and the bucket's registered decoders.
        """)

    encoded_data = content_property('encoded_data', doc="""
        The raw data stored in this object, essentially the encoded
        form of the `data` property. If unset, accessing this property
        will result in encoding the `data` property into a string. The
        encoding is dependent on the `content_type` property and the
        bucket's registered encoders.
        """)

    charset = content_property('charset', doc="""
        The character set of the encoded data as a string
        """)

    content_type = content_property('content_type', doc="""
        The MIME media type of the encoded data as a string
        """)

    content_encoding = content_property('content_encoding', doc="""
        The encoding (compression) of the encoded data. Valid values
        are identity, deflate, gzip
        """)

    last_modified = content_property('last_modified', """
        The UNIX timestamp of the modification time of this value.
        """)

    etag = content_property('etag', """
        A unique entity-tag for the value.
        """)

    usermeta = content_property('usermeta', doc="""
        Arbitrary user-defined metadata dict, mapping strings to strings.
        """)

    links = content_property('links', doc="""
        A set of bucket/key/tag 3-tuples representing links to other
        keys.
        """)

    indexes = content_property('indexes', doc="""
        The set of secondary index entries, consisting of
        index-name/value tuples
        """)

    get_encoded_data = content_method('get_encoded_data')
    set_encoded_data = content_method('set_encoded_data')
    add_index = content_method('add_index')
    remove_index = content_method('remove_index')
    remove_indexes = remove_index
    set_index = content_method('set_index')
    add_link = content_method('add_link')

    def _exists(self):
        if len(self.siblings) == 0:
            return False
        elif len(self.siblings) > 1:
            # Even if all of the siblings are tombstones, the object
            # essentially exists.
            return True
        else:
            return self.siblings[0].exists

    exists = property(_exists, None, doc="""
       Whether the object exists. This is only ``False`` when there
       are no siblings (the object was not found), or the solitary
       sibling is a tombstone.
       """)

    def _get_resolver(self):
        if callable(self._resolver):
            return self._resolver
        elif self._resolver is None:
            return self.bucket.resolver
        else:
            raise TypeError("resolver is not a function")

    def _set_resolver(self, value):
        if value is None or callable(value):
            self._resolver = value
        else:
            raise TypeError("resolver is not a function")

    resolver = property(_get_resolver, _set_resolver,
                        doc="""The sibling-resolution function for this
                           object. If the resolver is not set, the
                           bucket's resolver will be used.""")

    def get_sibling(self, index):
        deprecated("RiakObject.get_sibling is deprecated, use the "
                   "siblings property instead")
        return self.siblings[index]

    def store(self, w=None, dw=None, pw=None, return_body=True,
              if_none_match=False, timeout=None):
        """
        Store the object in Riak. When this operation completes, the
        object could contain new metadata and possibly new data if Riak
        contains a newer version of the object according to the object's
        vector clock.

        :param w: W-value, wait for this many partitions to respond
         before returning to client.
        :type w: integer
        :param dw: DW-value, wait for this many partitions to
         confirm the write before returning to client.
        :type dw: integer

        :param pw: PW-value, require this many primary partitions to
                   be available before performing the put
        :type pw: integer
        :param return_body: if the newly stored object should be
                            retrieved
        :type return_body: bool
        :param if_none_match: Should the object be stored only if
                              there is no key previously defined
        :type if_none_match: bool
        :param timeout: a timeout value in milliseconds
        :type timeout: int
        :rtype: :class:`RiakObject` """
        if len(self.siblings) != 1:
            raise ConflictError("Attempting to store an invalid object, "
                                "resolve the siblings first")

        self.client.put(self, w=w, dw=dw, pw=pw,
                        return_body=return_body,
                        if_none_match=if_none_match,
                        timeout=timeout)

        return self

    def reload(self, r=None, pr=None, timeout=None):
        """
        Reload the object from Riak. When this operation completes, the
        object could contain new metadata and a new value, if the object
        was updated in Riak since it was last retrieved.

        .. note:: Even if the key is not found in Riak, this will
           return a :class:`RiakObject`. Check the :attr:`exists`
           property to see if the key was found.

        :param r: R-Value, wait for this many partitions to respond
         before returning to client.
        :type r: integer
        :param pr: PR-value, require this many primary partitions to
                   be available before performing the read that
                   precedes the put
        :type pr: integer
        :param timeout: a timeout value in milliseconds
        :type timeout: int
        :rtype: :class:`RiakObject`
        """

        self.client.get(self, r=r, pr=pr, timeout=timeout)
        return self

    def delete(self, rw=None, r=None, w=None, dw=None, pr=None, pw=None,
               timeout=None):
        """
        Delete this object from Riak.

        :param rw: RW-value. Wait until this many partitions have
            deleted the object before responding. (deprecated in Riak
            1.0+, use R/W/DW)
        :type rw: integer
        :param r: R-value, wait for this many partitions to read object
         before performing the put
        :type r: integer
        :param w: W-value, wait for this many partitions to respond
         before returning to client.
        :type w: integer
        :param dw: DW-value, wait for this many partitions to
         confirm the write before returning to client.
        :type dw: integer
        :param pr: PR-value, require this many primary partitions to
                   be available before performing the read that
                   precedes the put
        :type pr: integer
        :param pw: PW-value, require this many primary partitions to
                   be available before performing the put
        :type pw: integer
        :param timeout: a timeout value in milliseconds
        :type timeout: int
        :rtype: :class:`RiakObject`
        """

        self.client.delete(self, rw=rw, r=r, w=w, dw=dw, pr=pr, pw=pw,
                           timeout=timeout)
        self.clear()
        return self

    def clear(self):
        """
        Reset this object.

        :rtype: RiakObject
        """
        self.siblings = []
        return self

    def add(self, *args):
        """
        Start assembling a Map/Reduce operation.
        A shortcut for :meth:`~riak.mapreduce.RiakMapReduce.add`.

        :rtype: :class:`~riak.mapreduce.RiakMapReduce`
        """
        mr = RiakMapReduce(self.client)
        mr.add(self.bucket.name, self.key)
        return mr.add(*args)

    def link(self, *args):
        """
        Start assembling a Map/Reduce operation.
        A shortcut for :meth:`~riak.mapreduce.RiakMapReduce.link`.

        :rtype: :class:`~riak.mapreduce.RiakMapReduce`
        """
        mr = RiakMapReduce(self.client)
        mr.add(self.bucket.name, self.key)
        return mr.link(*args)

    def map(self, *args):
        """
        Start assembling a Map/Reduce operation.
        A shortcut for :meth:`~riak.mapreduce.RiakMapReduce.map`.

        :rtype: :class:`~riak.mapreduce.RiakMapReduce`
        """
        mr = RiakMapReduce(self.client)
        mr.add(self.bucket.name, self.key)
        return mr.map(*args)

    def reduce(self, *args):
        """
        Start assembling a Map/Reduce operation.
        A shortcut for :meth:`~riak.mapreduce.RiakMapReduce.reduce`.

        :rtype: :class:`~riak.mapreduce.RiakMapReduce`
        """
        mr = RiakMapReduce(self.client)
        mr.add(self.bucket.name, self.key)
        return mr.reduce(*args)

from riak.mapreduce import RiakMapReduce
