import copy
import numpy as np

from collections import OrderedDict
from functools import wraps
import operator
import warnings

import conventions
from utils import expanded_indexer, safe_merge


class AttributesDict(OrderedDict):
    """A subclass of OrderedDict whose __setitem__ method automatically
    checks and converts values to be valid netCDF attributes
    """
    def __init__(self, *args, **kwds):
        OrderedDict.__init__(self, *args, **kwds)

    def __setitem__(self, key, value):
        if not conventions.is_valid_name(key):
            raise ValueError("Not a valid attribute name")
        # Strings get special handling because netCDF treats them as
        # character arrays. Everything else gets coerced to a numpy
        # vector. netCDF treats scalars as 1-element vectors. Arrays of
        # non-numeric type are not allowed.
        if isinstance(value, basestring):
            # netcdf attributes should be unicode
            value = unicode(value)
        else:
            try:
                value = conventions.coerce_type(np.atleast_1d(np.asarray(value)))
            except:
                raise ValueError("Not a valid value for a netCDF attribute")
            if value.ndim > 1:
                raise ValueError("netCDF attributes must be vectors " +
                        "(1-dimensional)")
            value = conventions.coerce_type(value)
            if str(value.dtype) not in conventions.TYPEMAP:
                # A plain string attribute is okay, but an array of
                # string objects is not okay!
                raise ValueError("Can not convert to a valid netCDF type")
        OrderedDict.__setitem__(self, key, value)

    def copy(self):
        """The copy method of the superclass simply calls the constructor,
        which in turn calls the update method, which in turns calls
        __setitem__. This subclass implementation bypasses the expensive
        validation in __setitem__ for a substantial speedup."""
        obj = self.__class__()
        for (attr, value) in self.iteritems():
            OrderedDict.__setitem__(obj, attr, copy.copy(value))
        return obj

    def __deepcopy__(self, memo=None):
        """
        Returns a deep copy of the current object.

        memo does nothing but is required for compatability with copy.deepcopy
        """
        return self.copy()

    def update(self, *other, **kwargs):
        """Set multiple attributes with a mapping object or an iterable of
        key/value pairs"""
        # Capture arguments in an OrderedDict
        args_dict = OrderedDict(*other, **kwargs)
        try:
            # Attempt __setitem__
            for (attr, value) in args_dict.iteritems():
                self.__setitem__(attr, value)
        except:
            # A plain string attribute is okay, but an array of
            # string objects is not okay!
            raise ValueError("Can not convert to a valid netCDF type")
            # Clean up so that we don't end up in a partial state
            for (attr, value) in args_dict.iteritems():
                if self.__contains__(attr):
                    self.__delitem__(attr)
            # Re-raise
            raise

    def __eq__(self, other):
        if not set(self.keys()) == set(other.keys()):
            return False
        for (key, value) in self.iteritems():
            if value.__class__ != other[key].__class__:
                return False
            if isinstance(value, basestring):
                if value != other[key]:
                    return False
            else:
                if value.tostring() != other[key].tostring():
                    return False
        return True


def _as_compatible_data(data):
    """If data does not have the necessary attributes to be the private _data
    attribute, convert it to a np.ndarray and raise an warning
    """
    # don't check for __len__ or __iter__ so as not to warn if data is a numpy
    # numeric type like np.float32
    required = ['dtype', 'shape', 'size', 'ndim']
    if not all(hasattr(data, attr) for attr in required):
        warnings.warn('converting data to np.ndarray because it lacks some of '
                      'the necesssary attributes for lazy use', RuntimeWarning,
                      stacklevel=3)
        data = np.asarray(data)
    return data


class Variable(object):
    """
    A netcdf-like variable consisting of dimensions, data and attributes
    which describe a single varRiable.  A single variable object is not
    fully described outside the context of its parent Dataset.
    """
    def __init__(self, dims, data, attributes=None):
        data = _as_compatible_data(data)
        if len(dims) != data.ndim:
            raise ValueError('data must have same shape as the number of '
                             'dimensions')
        self._dimensions = tuple(dims)
        self._data = data
        if attributes is None:
            attributes = {}
        self._attributes = AttributesDict(attributes)

    @property
    def dimensions(self):
        return self._dimensions

    @property
    def data(self):
        """
        The variable's data as a numpy.ndarray
        """
        if not isinstance(self._data, np.ndarray):
            self._data = np.asarray(self._data[...])
        return self._data

    @data.setter
    def data(self, value):
        value = np.asarray(value)
        if value.shape != self.shape:
            raise ValueError("replacement data must match the Variable's "
                             "shape")
        self._data = value

    @property
    def dtype(self):
        return self._data.dtype

    @property
    def shape(self):
        return self._data.shape

    @property
    def size(self):
        return self._data.size

    @property
    def ndim(self):
        return self._data.ndim

    def __len__(self):
        return len(self._data)

    def __nonzero__(self):
        if self.size == 1:
            return bool(self.data)
        else:
            raise ValueError('ValueError: The truth value of variable with '
                             'more than one element is ambiguous.')

    def __getitem__(self, key):
        """
        Return a new Variable object whose contents are consistent with getting
        the provided key from the underlying data
        """
        key = expanded_indexer(key, self.ndim)
        dimensions = [dim for k, dim in zip(key, self.dimensions)
                      if not isinstance(k, int)]
        return Variable(dimensions, self._data[key], self.attributes)

    def __setitem__(self, key, value):
        """__setitem__ is overloaded to access the underlying numpy data"""
        self.data[key] = value

    def __iter__(self):
        """
        Iterate over the contents of this Variable
        """
        for n in range(len(self)):
            yield self[n]

    @property
    def attributes(self):
        return self._attributes

    def copy(self):
        """
        Returns a shallow copy of the current object.
        """
        return self.__copy__()

    def _copy(self, deepcopy=False):
        # deepcopies should always be of a numpy view of the data, not the data
        # itself, because non-memory backends don't necessarily have deepcopy
        # defined sensibly (this is a problem for netCDF4 variables)
        data = copy.deepcopy(self.data) if deepcopy else self._data
        # note:
        # dimensions is already an immutable tuple
        # attributes will be copied when the new Variable is created
        return Variable(self.dimensions, data, self.attributes)

    def __copy__(self):
        """
        Returns a shallow copy of the current object.
        """
        return self._copy(deepcopy=False)

    def __deepcopy__(self, memo=None):
        """
        Returns a deep copy of the current object.

        memo does nothing but is required for compatability with copy.deepcopy
        """
        return self._copy(deepcopy=True)

    # mutable objects should not be hashable
    __hash__ = None

    def __str__(self):
        """Create a ncdump-like summary of the object"""
        summary = ["dimensions:"]
        # prints dims that look like:
        #    dimension = length
        dim_print = lambda d, l : "\t%s : %s" % (conventions.pretty_print(d, 30),
                                                 conventions.pretty_print(l, 10))
        # add each dimension to the summary
        summary.extend([dim_print(d, l) for d, l in zip(self.dimensions, self.shape)])
        summary.append("\ndtype : %s" % (conventions.pretty_print(self.dtype, 8)))
        summary.append("\nattributes:")
        #    attribute:value
        summary.extend(["\t%s:%s" % (conventions.pretty_print(att, 30),
                                     conventions.pretty_print(val, 30))
                        for att, val in self.attributes.iteritems()])
        # create the actual summary
        return '\n'.join(summary)

    def views(self, slicers):
        """Return a new Variable object whose contents are a view of the object
        sliced along a specified dimension.

        Parameters
        ----------
        slicers : {dim: slice, ...}
            A dictionary mapping from dim to slice, dim represents
            the dimension to slice along slice represents the range of the
            values to extract.

        Returns
        -------
        obj : Variable object
            The returned object has the same attributes and dimensions
            as the original. Data contents are taken along the
            specified dimension.  Care must be taken since modifying (most)
            values in the returned object will result in modification to the
            parent object.

        See Also
        --------
        view
        take
        """
        slices = [slice(None)] * self.data.ndim
        for i, dim in enumerate(self.dimensions):
            if dim in slicers:
                slices[i] = slicers[dim]
        return self[tuple(slices)]

    def view(self, s, dim):
        """Return a new Variable object whose contents are a view of the object
        sliced along a specified dimension.

        Parameters
        ----------
        s : slice
            The slice representing the range of the values to extract.
        dim : string
            The dimension to slice along.

        Returns
        -------
        obj : Variable object
            The returned object has the same attributes and dimensions
            as the original. Data contents are taken along the
            specified dimension.  Care must be taken since modifying (most)
            values in the returned object will result in modification to the
            parent object.

        See Also
        --------
        take
        """
        return self.views({dim: s})

    def take(self, indices, dim):
        """Return a new Variable object whose contents are sliced from
        the current object along a specified dimension

        Parameters
        ----------
        indices : array_like
            The indices of the values to extract. indices must be compatible
            with the ndarray.take() method.
        dim : string
            The dimension to slice along. If multiple dimensions equal
            dim (e.g. a correlation matrix), then the slicing is done
            only along the first matching dimension.

        Returns
        -------
        obj : Variable object
            The returned object has the same attributes and dimensions
            as the original. Data contents are taken along the
            specified dimension.

        See Also
        --------
        numpy.take
        """
        indices = np.asarray(indices)
        if indices.ndim != 1:
            raise ValueError('indices should have a single dimension')
        # When dim appears repeatedly in self.dimensions, using the index()
        # method gives us only the first one, which is the desired behavior
        axis = self.dimensions.index(dim)
        # take only works on actual numpy arrays
        data = self.data.take(indices, axis=axis)
        return Variable(self.dimensions, data, self.attributes)


def broadcast_var_data(self, other):
    self_data = self.data
    if hasattr(other, 'dimensions'):
        # build dimensions for new Variable
        other_only_dims = [dim for dim in other.dimensions
                           if dim not in self.dimensions]
        dimensions = list(self.dimensions) + other_only_dims

        # expand self_data's dimensions so it's broadcast compatible after
        # adding other's dimensions to the end
        for _ in xrange(len(other_only_dims)):
            self_data = np.expand_dims(self_data, axis=-1)

        # expand and reorder other_data so the dimensions line up
        self_only_dims = [dim for dim in dimensions
                          if dim not in other.dimensions]
        other_data = other.data
        for _ in xrange(len(self_only_dims)):
            other_data = np.expand_dims(other_data, axis=-1)
        other_dims = list(other.dimensions) + self_only_dims
        axes = [other_dims.index(dim) for dim in dimensions]
        other_data = other_data.transpose(axes)
    else:
        # rely on numpy broadcasting rules
        other_data = other
        dimensions = self.dimensions
    return self_data, other_data, dimensions


def unary_op_wrapper(name):
    f = getattr(operator, '__%s__' % name)
    @wraps(f)
    def func(self):
        obj = self.copy()
        obj.data = f(self.data[:])
        return obj
    return func


def inplace_binary_op(name):
    f = getattr(operator, '__i%s__' % name)
    @wraps(f)
    def func(self, other):
        self_data, other_data, dimensions = broadcast_var_data(self, other)
        if dimensions != self.dimensions:
            raise ValueError('dimensions cannot change for in-place operations')
        self.data = f(self_data, other_data)
        return self
    return func


def binary_op(name, reflexive=False):
    f = getattr(operator, '__%s__' % name)
    @wraps(f)
    def func(self, other):
        self_data, other_data, new_dims = broadcast_var_data(self, other)
        new_data = (f(self_data, other_data)
                    if not reflexive
                    else f(other_data, self_data))
        all_attributes = (getattr(v, 'attributes') for v in [self, other]
                          if hasattr(v, 'attributes'))
        new_attr = safe_merge(*all_attributes)
        return Variable(new_dims, new_data, new_attr)
    return func


UNARY_OPS = ['neg', 'pos', 'abs', 'invert']
CMP_BINARY_OPS = ['lt', 'le', 'eq', 'ne', 'ge', 'gt']
NUM_BINARY_OPS = ['add', 'sub', 'mul', 'div', 'truediv', 'floordiv', 'mod',
                  'pow', 'and', 'xor', 'or']


def inject_special_operations(cls, priority=10):
    # priortize our operations over numpy.ndarray's (priority=1.0)
    cls.__array_priority__ = priority

    for name in UNARY_OPS:
        setattr(cls, '__%s__' % name, unary_op_wrapper(name))
    for name in CMP_BINARY_OPS:
        setattr(cls, '__%s__' % name, binary_op(name))
    for name in NUM_BINARY_OPS:
        setattr(cls, '__%s__' % name, binary_op(name))
        setattr(cls, '__i%s__' % name, inplace_binary_op(name))
        setattr(cls, '__r%s__' % name, binary_op(name, reflexive=True))


inject_special_operations(Variable)
