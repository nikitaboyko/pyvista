"""
Attributes common to PolyData and Grid Objects
"""
import logging
from weakref import proxy

import numpy as np
import vtk
from vtk.util.numpy_support import vtk_to_numpy
from vtk.util.numpy_support import numpy_to_vtk

import vtki
from vtki.utilities import (get_scalar, POINT_DATA_FIELD, CELL_DATA_FIELD,
                            vtk_bit_array_to_char)
from vtki import DataSetFilters
from vtki import plot

log = logging.getLogger(__name__)
log.setLevel('CRITICAL')


class Common(DataSetFilters):
    """ Methods in common to grid and surface objects"""

    # Simply bind vtki.plotting.plot to the object
    plot = plot

    def __init__(self, *args, **kwargs):
        self.references = []
        self._point_bool_array_names = []
        self._cell_bool_array_names = []

    @property
    def active_scalar_info(self):
        """Return the active scalar's field and name: [field, name]"""
        if not hasattr(self, '_active_scalar_info'):
            self._active_scalar_info = [POINT_DATA_FIELD, None] # field and name
        field, name = self._active_scalar_info

        # rare error where scalar name isn't a valid scalar
        if name not in self.point_arrays:
            if name not in self.cell_arrays:
                name = None

        if name is None:
            if self.n_scalars < 1:
                return field, name
            # find some array in the set field
            parr = self.GetPointData().GetArrayName(0)
            carr = self.GetCellData().GetArrayName(0)
            if parr is not None:
                self._active_scalar_info = [POINT_DATA_FIELD, parr]
            elif carr is not None:
                self._active_scalar_info = [CELL_DATA_FIELD, carr]
        return self._active_scalar_info

    @property
    def active_scalar_name(self):
        """Returns the active scalar's name"""
        return self.active_scalar_info[1]

    @property
    def points(self):
        """ returns a pointer to the points as a numpy object """
        return vtk_to_numpy(self.GetPoints().GetData())

    @points.setter
    def points(self, points):
        """ set points without copying """
        if not isinstance(points, np.ndarray):
            raise TypeError('Points must be a numpy array')
        vtk_points = vtki.vtk_points(points, False)
        self.SetPoints(vtk_points)

    @property
    def t_coords(self):
        if self.GetPointData().GetTCoords() is not None:
            return vtk_to_numpy(self.GetPointData().GetTCoords())
        return None

    @t_coords.setter
    def t_coords(self, t_coords):
        if not isinstance(t_coords, np.ndarray):
            raise TypeError('Texture coordinates must be a numpy array')
        if t_coords.ndim != 2:
            raise AssertionError('Texture coordinates must by a 2-dimensional array')
        if t_coords.shape[0] != self.n_points:
            raise AssertionError('Number of texture coordinates ({}) must match number of points ({})'.format(t_coords.shape[0], self.n_points))
        if t_coords.shape[1] != 2:
            raise AssertionError('Texture coordinates must only have 2 components, not ({})'.format(t_coords.shape[1]))
        if np.min(t_coords) < 0.0 or np.max(t_coords) > 1.0:
            raise AssertionError('Texture coordinates must be within (0, 1) range.')
        # convert the array
        vtkarr = numpy_to_vtk(t_coords)
        vtkarr.SetName('Texture Coordinates')
        self.GetPointData().SetTCoords(vtkarr)
        return

    @property
    def textures(self):
        """A dictionary to hold ``vtk.vtkTexture`` objects that can be
        associated with this dataset. When casting back to a VTK dataset or
        filtering this dataset, these textures will not be passed.
        """
        if not hasattr(self, '_textures'):
            self._textures = {}
        return self._textures

    def _activate_texture(mesh, name):
        """Grab a texture and update the active texture coordinates. This makes
        sure to not destroy onld texture coordinates

        Parameters
        ----------
        name : str
            The name of the texture and texture coordinates to activate

        Return
        ------
        vtk.vtkTexture : The active texture
        """
        if name == True:
            # Grab the first name availabe if True
            try:
                name = list(mesh.textures.keys())[0]
            except IndexError:
                logging.warning('No textures associated with input mesh.')
                return None
        # Grab the texture object by name
        try:
            texture = mesh.textures[name]
        except KeyError:
            logging.warning('Texture ({}) not associated with this dataset'.format(name))
            texture = None
        else:
            # Be sure to reset the tcoords if present
            # Grab old coordinates
            if name in mesh.scalar_names:
                old_tcoord = mesh.GetPointData().GetTCoords()
                mesh.GetPointData().SetTCoords(mesh.GetPointData().GetArray(name))
                mesh.GetPointData().AddArray(old_tcoord)
        return texture

    def set_active_scalar(self, name, preference='cell'):
        """Finds the scalar by name and appropriately sets it as active"""
        arr, field = get_scalar(self, name, preference=preference, info=True)
        if field == POINT_DATA_FIELD:
            self.GetPointData().SetActiveScalars(name)
        elif field == CELL_DATA_FIELD:
            self.GetCellData().SetActiveScalars(name)
        else:
            raise RuntimeError('Data field ({}) no useable'.format(field))
        self._active_scalar_info = [field, name]

    def change_scalar_name(self, old_name, new_name, preference='cell'):
        """Changes array name by searching for the array then renaming it"""
        _, field = get_scalar(self, old_name, preference=preference, info=True)
        if field == POINT_DATA_FIELD:
            self.GetPointData().GetArray(old_name).SetName(new_name)
        elif field == CELL_DATA_FIELD:
            self.GetCellData().GetArray(old_name).SetName(new_name)
        else:
            raise RuntimeError('Array not found.')
        if self.active_scalar_info[1] == old_name:
            self.set_active_scalar(new_name, preference=field)


    @property
    def active_scalar(self):
        """Returns the active scalar as an array"""
        field, name = self.active_scalar_info
        if name is None:
            return None
        if field == POINT_DATA_FIELD:
            return self._point_scalar(name)
        elif field == CELL_DATA_FIELD:
            return self._cell_scalar(name)

    def _point_scalar(self, name=None):
        """
        Returns point scalars of a vtk object

        Parameters
        ----------
        name : str
            Name of point scalars to retrive.

        Returns
        -------
        scalars : np.ndarray
            Numpy array of scalars

        """
        if name is None:
            # use active scalar array
            field, name = self.active_scalar_info
            if field != POINT_DATA_FIELD:
                raise RuntimeError('Must specify an array to fetch.')
        vtkarr = self.GetPointData().GetArray(name)
        assert vtkarr is not None, '%s is not a point scalar' % name

        # numpy does not support bit array data types
        if isinstance(vtkarr, vtk.vtkBitArray):
            vtkarr = vtk_bit_array_to_char(vtkarr)
            if name not in self._point_bool_array_names:
                self._point_bool_array_names.append(name)

        array = vtk_to_numpy(vtkarr)
        if array.dtype == np.uint8 and name in self._point_bool_array_names:
            array = array.view(np.bool)
        return array

    def _add_point_scalar(self, scalars, name, set_active=False, deep=True):
        """
        Adds point scalars to the mesh

        Parameters
        ----------
        scalars : numpy.ndarray
            Numpy array of scalars.  Must match number of points.

        name : str
            Name of point scalars to add.

        set_active : bool, optional
            Sets the scalars to the active plotting scalars.  Default False.

        deep : bool, optional
            Does not copy scalars when False.  A reference to the scalars
            must be kept to avoid a segfault.

        """
        if not isinstance(scalars, np.ndarray):
            raise TypeError('Input must be a numpy.ndarray')

        if scalars.shape[0] != self.n_points:
            raise Exception('Number of scalars must match the number of ' +
                            'points')

        # need to track which arrays are boolean as all boolean arrays
        # must be stored as uint8
        if scalars.dtype == np.bool:
            scalars = scalars.view(np.uint8)
            if name not in self._point_bool_array_names:
                self._point_bool_array_names.append(name)

        if not scalars.flags.c_contiguous:
            scalars = np.ascontiguousarray(scalars)

        vtkarr = numpy_to_vtk(scalars, deep=deep)
        vtkarr.SetName(name)
        self.GetPointData().AddArray(vtkarr)
        if set_active:
            self.GetPointData().SetActiveScalars(name)
            self._active_scalar_info = [POINT_DATA_FIELD, name]

    def points_to_double(self):
        """ Makes points double precision """
        if self.points.dtype != np.double:
            self.points = self.points.astype(np.double)

    def rotate_x(self, angle):
        """
        Rotates mesh about the x-axis.

        Parameters
        ----------
        angle : float
            Angle in degrees to rotate about the x-axis.

        """
        axis_rotation(self.points, angle, inplace=True, axis='x')

    def rotate_y(self, angle):
        """
        Rotates mesh about the y-axis.

        Parameters
        ----------
        angle : float
            Angle in degrees to rotate about the y-axis.

        """
        axis_rotation(self.points, angle, inplace=True, axis='y')

    def rotate_z(self, angle):
        """
        Rotates mesh about the z-axis.

        Parameters
        ----------
        angle : float
            Angle in degrees to rotate about the z-axis.

        """
        axis_rotation(self.points, angle, inplace=True, axis='z')

    def translate(self, xyz):
        """
        Translates the mesh.

        Parameters
        ----------
        xyz : list or np.ndarray
            Length 3 list or array.

        """
        self.points += np.asarray(xyz)

    def transform(self, trans):
        """
        Compute a transformation in place using a 4x4 transform.

        Parameters
        ----------
        trans : vtk.vtkMatrix4x4, vtk.vtkTransform, or np.ndarray
            Accepts a vtk transformation object or a 4x4 transformation matrix.

        """
        if isinstance(trans, vtk.vtkMatrix4x4):
            t = vtki.trans_from_matrix(trans)
        elif isinstance(trans, vtk.vtkTransform):
            t = vtki.trans_from_matrix(trans.GetMatrix())
        elif isinstance(trans, np.ndarray):
            if trans.shape[0] != 4 or trans.shape[1] != 4:
                raise Exception('Transformation array must be 4x4')
            t = trans
        else:
            raise TypeError('Input transform must be either:\n'
                            + '\tvtk.vtkMatrix4x4\n'
                            + '\tvtk.vtkTransform\n'
                            + '\t4x4 np.ndarray\n')

        x = (self.points*t[0, :3]).sum(1) + t[0, -1]
        y = (self.points*t[1, :3]).sum(1) + t[1, -1]
        z = (self.points*t[2, :3]).sum(1) + t[2, -1]

        # overwrite points
        self.points[:, 0] = x
        self.points[:, 1] = y
        self.points[:, 2] = z

    def _cell_scalar(self, name=None):
        """
        Returns the cell scalars of a vtk object

        Parameters
        ----------
        name : str
            Name of cell scalars to retrive.

        Returns
        -------
        scalars : np.ndarray
            Numpy array of scalars

        """
        if name is None:
            # use active scalar array
            field, name = self.active_scalar_info
            if field != CELL_DATA_FIELD:
                raise RuntimeError('Must specify an array to fetch.')

        vtkarr = self.GetCellData().GetArray(name)
        assert vtkarr is not None, '%s is not a cell scalar' % name

        # numpy does not support bit array data types
        if isinstance(vtkarr, vtk.vtkBitArray):
            vtkarr = vtk_bit_array_to_char(vtkarr)
            if name not in self._cell_bool_array_names:
                self._cell_bool_array_names.append(name)

        array = vtk_to_numpy(vtkarr)
        if array.dtype == np.uint8 and name in self._cell_bool_array_names:
            array = array.view(np.bool)
        return array

    def _add_cell_scalar(self, scalars, name, set_active=False, deep=True):
        """
        Adds cell scalars to the vtk object.

        Parameters
        ----------
        scalars : numpy.ndarray
            Numpy array of scalars.  Must match number of points.

        name : str
            Name of point scalars to add.

        set_active : bool, optional
            Sets the scalars to the active plotting scalars.  Default False.

        deep : bool, optional
            Does not copy scalars when False.  A reference to the scalars
            must be kept to avoid a segfault.

        """
        if not isinstance(scalars, np.ndarray):
            raise TypeError('Input must be a numpy.ndarray')

        if scalars.shape[0] != self.n_cells:
            raise Exception('Number of scalars must match the number of cells (%d)'
                            % self.n_cells)

        assert scalars.flags.c_contiguous, 'Array must be contigious'
        if scalars.dtype == np.bool:
            scalars = scalars.view(np.uint8)
            self._cell_bool_array_names.append(name)

        vtkarr = numpy_to_vtk(scalars, deep=deep)
        vtkarr.SetName(name)
        self.GetCellData().AddArray(vtkarr)
        if set_active:
            self.GetCellData().SetActiveScalars(name)
            self._active_scalar_info = [CELL_DATA_FIELD, name]

    def copy_meta_from(self, ido):
        """Copies vtki meta data onto this object from another object"""
        self._active_scalar_info = ido.active_scalar_info

    def copy(self, deep=True):
        """
        Returns a copy of the object

        Parameters
        ----------
        deep : bool, optional
            When True makes a full copy of the object.

        Returns
        -------
        newobject : same as input
           Deep or shallow copy of the input.
        """
        thistype = type(self)
        newobject = thistype()
        if deep:
            newobject.DeepCopy(self)
        else:
            newobject.ShallowCopy(self)
        newobject.copy_meta_from(self)
        return newobject


    def _remove_point_scalar(self, key):
        """ removes point scalars from point data """
        self.GetPointData().RemoveArray(key)

    @property
    def point_arrays(self):
        """ Returns the all point arrays """
        pdata = self.GetPointData()
        narr = pdata.GetNumberOfArrays()

        # Update data if necessary
        if hasattr(self, '_point_arrays'):
            keys = list(self._point_arrays.keys())
            if narr == len(keys):
                if keys:
                    if self._point_arrays[keys[0]].size == self.n_points:
                        return self._point_arrays
                else:
                    return self._point_arrays

        # dictionary with callbacks
        self._point_arrays = PointScalarsDict(self)

        for i in range(narr):
            name = pdata.GetArrayName(i)
            self._point_arrays[name] = self._point_scalar(name)

        self._point_arrays.enable_callback()
        return self._point_arrays

    def _remove_cell_scalar(self, key):
        """ removes cell scalars """
        self.GetCellData().RemoveArray(key)

    @property
    def cell_arrays(self):
        """ Returns the all cell arrays """
        cdata = self.GetCellData()
        narr = cdata.GetNumberOfArrays()

        # Update data if necessary
        if hasattr(self, '_cell_arrays'):
            keys = list(self._cell_arrays.keys())
            if narr == len(keys):
                if keys:
                    if self._cell_arrays[keys[0]].size == self.n_cells:
                        return self._cell_arrays
                else:
                    return self._cell_arrays

        # dictionary with callbacks
        self._cell_arrays = CellScalarsDict(self)

        for i in range(narr):
            name = cdata.GetArrayName(i)
            self._cell_arrays[name] = self._cell_scalar(name)

        self._cell_arrays.enable_callback()
        return self._cell_arrays

    @property
    def n_points(self):
        return self.GetNumberOfPoints()

    @property
    def n_cells(self):
        return self.GetNumberOfCells()

    @property
    def number_of_points(self):
        """ returns the number of points """
        return self.GetNumberOfPoints()

    @property
    def number_of_cells(self):
        """ returns the number of cells """
        return self.GetNumberOfCells()

    @property
    def bounds(self):
        return self.GetBounds()

    @property
    def center(self):
        return self.GetCenter()

    @property
    def extent(self):
        return self.GetExtent()

    def get_data_range(self, arr=None, preference='cell'):
        if arr is None:
            # use active scalar array
            _, arr = self.active_scalar_info
        if isinstance(arr, str):
            arr = get_scalar(self, arr, preference=preference)
        # If array has no tuples return a NaN range
        if arr is None or arr.size == 0:
            return (np.nan, np.nan)
        # Use the array range
        return np.nanmin(arr), np.nanmax(arr)

    def get_scalar(self, name, preference='cell', info=False):
        """ Searches both point and cell data for an array """
        return get_scalar(self, name, preference=preference, info=info)

    @property
    def n_scalars(self):
        return self.GetPointData().GetNumberOfArrays() + \
               self.GetCellData().GetNumberOfArrays()

    @property
    def scalar_names(self):
        """A list of scalar names for the dataset. This makes
        sure to put the active scalar's name first in the list."""
        names = []
        for i in range(self.GetPointData().GetNumberOfArrays()):
            names.append(self.GetPointData().GetArrayName(i))
        for i in range(self.GetCellData().GetNumberOfArrays()):
            names.append(self.GetCellData().GetArrayName(i))
        try:
            names.remove(self.active_scalar_name)
            names.insert(0, self.active_scalar_name)
        except ValueError:
            pass
        return names


    def _get_attrs(self):
        """An internal helper for the representation methods"""
        attrs = []
        attrs.append(("N Cells", self.GetNumberOfCells(), "{}"))
        attrs.append(("N Points", self.GetNumberOfPoints(), "{}"))
        bds = self.bounds
        attrs.append(("X Bounds", (bds[0], bds[1]), "{:.3e}, {:.3e}"))
        attrs.append(("Y Bounds", (bds[2], bds[3]), "{:.3e}, {:.3e}"))
        attrs.append(("Z Bounds", (bds[4], bds[5]), "{:.3e}, {:.3e}"))
        return attrs


    def head(self, display=True, html=None):
        """Return the header stats of this dataset. If in IPython, this will
        be formatted to HTML. Otherwise returns a console friendly string"""
        try:
            __IPYTHON__
            ipy = True
        except NameError:
            ipy = False
        if html == True:
            ipy = True
        elif html == False:
            ipy = False
        # Generate the output
        if ipy:
            fmt = ""
            # HTML version
            fmt += "\n"
            fmt += "<table>\n"
            fmt += "<tr><th>{}</th><th>Information</th></tr>\n".format(self.GetClassName())
            row = "<tr><td>{}</td><td>{}</td></tr>\n"
            # now make a call on the object to get its attributes as a list of len 2 tuples
            for attr in self._get_attrs():
                try:
                    fmt += row.format(attr[0], attr[2].format(*attr[1]))
                except:
                    fmt += row.format(attr[0], attr[2].format(attr[1]))
            fmt += row.format('N Scalars', self.n_scalars)
            fmt += "</table>\n"
            fmt += "\n"
            if display:
                from IPython.display import display, HTML
                display(HTML(fmt))
                return
            return fmt
        # Otherwise return a string that is Python console friendly
        fmt = "{} ({})\n".format(self.GetClassName(), hex(id(self)))
        # now make a call on the object to get its attributes as a list of len 2 tuples
        row = "  {}:\t{}\n"
        for attr in self._get_attrs():
            try:
                fmt += row.format(attr[0], attr[2].format(*attr[1]))
            except:
                fmt += row.format(attr[0], attr[2].format(attr[1]))
        fmt += row.format('N Scalars', self.n_scalars)
        return fmt


    def _repr_html_(self):
        """A pretty representation for Jupyter notebooks that includes header
        details and information about all scalar arrays"""
        fmt = ""
        if self.n_scalars > 0:
            fmt += "<table>"
            fmt += "<tr><th>Header</th><th>Data Arrays</th></tr>"
            fmt += "<tr><td>"
        # Get the header info
        fmt += self.head(display=False, html=True)
        # Fill out scalar arrays
        if self.n_scalars > 0:
            fmt += "</td><td>"
            fmt += "\n"
            fmt += "<table>\n"
            row = "<tr><th>{}</th><th>{}</th><th>{}</th><th>{}</th><th>{}</th></tr>\n"
            fmt += row.format("Name", "Field", "Type", "Min", "Max")
            row = "<tr><td>{}</td><td>{}</td><td>{}</td><td>{:.3e}</td><td>{:.3e}</td></tr>\n"

            def format_array(key, field):
                arr = get_scalar(self, key)
                dl, dh = self.get_data_range(key)
                if key == self.active_scalar_info[1]:
                    key = '<b>{}</b>'.format(key)
                return row.format(key, field, arr.dtype, dl, dh)

            for i in range(self.GetPointData().GetNumberOfArrays()):
                key = self.GetPointData().GetArrayName(i)
                fmt += format_array(key, field='Points')
            for i in range(self.GetCellData().GetNumberOfArrays()):
                key = self.GetCellData().GetArrayName(i)
                fmt += format_array(key, field='Cells')
            fmt += "</table>\n"
            fmt += "\n"
            fmt += "</td></tr> </table>"
        return fmt


class CellScalarsDict(dict):
    """
    Updates internal cell data when an array is added or removed from
    the dictionary.
    """

    def __init__(self, data):
        self.data = proxy(data)
        dict.__init__(self)
        self.callback_enabled = False

    def enable_callback(self):
        self.callback_enabled = True

    def __setitem__(self, key, val):
        """ overridden to assure data is contigious """
        if self.callback_enabled:
            self.data._add_cell_scalar(val, key, deep=False)
        dict.__setitem__(self, key, val)

    def __delitem__(self, key):
        self.data._remove_cell_scalar(key)
        return dict.__delitem__(self, key)


class PointScalarsDict(dict):
    """
    Updates internal point data when an array is added or removed from
    the dictionary.
    """

    def __init__(self, data):
        self.data = proxy(data)
        dict.__init__(self)
        self.callback_enabled = False

    def enable_callback(self):
        self.callback_enabled = True

    def __setitem__(self, key, val):
        """ overridden to assure data is contigious """
        if self.callback_enabled:
            self.data._add_point_scalar(val, key, deep=False)
        dict.__setitem__(self, key, val)

    def __delitem__(self, key):
        self.data._remove_point_scalar(key)
        return dict.__delitem__(self, key)


def axis_rotation(p, ang, inplace=False, deg=True, axis='z'):
    """ Rotates points p angle ang (in deg) about an axis """
    axis = axis.lower()

    # Copy original array to if not inplace
    if not inplace:
        p = p.copy()

    # Convert angle to radians
    if deg:
        ang *= np.pi / 180

    if axis == 'x':
        y = p[:, 1] * np.cos(ang) - p[:, 2] * np.sin(ang)
        z = p[:, 1] * np.sin(ang) + p[:, 2] * np.cos(ang)
        p[:, 1] = y
        p[:, 2] = z
    elif axis == 'y':
        x = p[:, 0] * np.cos(ang) + p[:, 2] * np.sin(ang)
        z = - p[:, 0] * np.sin(ang) + p[:, 2] * np.cos(ang)
        p[:, 0] = x
        p[:, 2] = z
    elif axis == 'z':
        x = p[:, 0] * np.cos(ang) - p[:, 1] * np.sin(ang)
        y = p[:, 0] * np.sin(ang) + p[:, 1] * np.cos(ang)
        p[:, 0] = x
        p[:, 1] = y
    else:
        raise Exception('invalid axis.  Must be either "x", "y", or "z"')

    if not inplace:
        return p