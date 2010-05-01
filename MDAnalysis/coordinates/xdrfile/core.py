"""Reading of Gromacs trajectories."""

import os.path
import errno
import numpy

import libxdrfile, statno
from MDAnalysis.coordinates import DCD

try:
    from numpy import rad2deg   # numpy 1.3+
except ImportError:
    def rad2deg(x):             # no need for the numpy out=[] argument 
        return 180.0*x/numpy.pi

class Timestep(DCD.Timestep):
    """Timestep for a Gromacs trajectory."""
    def __init__(self, arg):
        DIM = libxdrfile.DIM    # compiled-in dimension (most likely 3)
        if numpy.dtype(type(arg)) == numpy.dtype(int):
            self.frame = 0
            self.numatoms = arg
            # C floats and C-order for arrays (see libxdrfile.i)
            self._pos = numpy.zeros((self.numatoms, DIM), dtype=numpy.float32, order='C')            
            self._unitcell = numpy.zeros((DIM,DIM), dtype=numpy.float32)
            # additional data for xtc
            self.status = libxdrfile.exdrOK
            self.step = 0
            self.time = 0
            self.prec = 0
        elif isinstance(arg, Timestep): # Copy constructor
            # This makes a deepcopy of the timestep
            self.frame = arg.frame
            self.numatoms = arg.numatoms
            self._unitcell = numpy.array(arg._unitcell)
            self._pos = numpy.array(arg._pos)
            for attr in ('status', 'step', 'time', 'prec', 'lmbda'):
                if hasattr(arg, attr):
                    self.__setattr__(attr, arg.__getattribute__(attr))
        elif isinstance(arg, numpy.ndarray):
            if len(arg.shape) != 2: raise Exception("numpy array can only have 2 dimensions")
            self._unitcell = numpy.zeros((DIM,DIM), dtype=numpy.float32)
            self.frame = 0
            if arg.shape[0] == DIM:    ## wrong order
                self.numatoms = arg.shape[-1]
            else: 
                self.numatoms = arg.shape[0]
            self._pos = arg.copy('C')  ## C-order ! (?) -- does this work or do I have to transpose?
            # additional data for xtc
            self.status = libxdrfile.exdrOK
            self.step = 0
            self.time = 0
            self.prec = 0
        else: 
            raise Exception("Cannot create an empty Timestep")
        self._x = self._pos[:,0]
        self._y = self._pos[:,1]
        self._z = self._pos[:,2]

    @property
    def dimensions(self):
        """unitcell dimensions (A, B, C, alpha, beta, gamma)

        - A, B, C are the lengths of the primitive cell vectors e1, e2, e3
        - alpha = angle(e1, e2)
        - beta = angle(e1, e3)
        - gamma = angle(e2, e3)
        """
        # Layout of unitcell is [X, Y, Z] with the primitive cell vectors
        x = self._unitcell[:,0]
        y = self._unitcell[:,1]
        z = self._unitcell[:,2]
        A, B, C = [_veclength(v) for v in x,y,z]
        alpha =  _angle(x,y)
        beta  =  _angle(x,z)
        gamma =  _angle(y,z)
        return numpy.array([A,B,C,alpha,beta,gamma])

def _veclength(v):
    return numpy.sqrt(numpy.dot(v,v))

def _angle(a,b):
    angle = numpy.arccos(numpy.dot(a,b) / (_veclength(a)*_veclength(b)))
    return rad2deg(angle)


class TrjReader(object):
    """Generic base class for reading Gromacs trajectories inside MDAnalysis.

    Derive classes and set :attr:`TrjReader.format`,
    :attr:`TrjReader._read_trj` and :attr:`TrjReader._read_trj_atoms`.

    Example::
       reader = TrjReader("file.trj")
       for ts in reader:
          print ts
    """
    #: override to define trajectory format of the reader (XTC or TRR)
    format = None
    #: supply the appropriate Timestep class, e.g. 
    #: :class:`MDAnalysis.coordinates.xdrfile.XTC.Timestep` for XTC
    _Timestep = Timestep

    def __init__(self, filename):
        self.filename = filename
        self.xdrfile = None
        self.__numatoms = None
        self.numframes = 0
        self.fixed = 0          # not relevant for Gromacs xtc/trr
        self.skip = 1
        self.periodic = False
        self.ts = self._Timestep(self.numatoms)
        # Read in the first timestep
        self._read_next_timestep()

    @property
    def numatoms(self):
        """Read the number of atoms from the trajectory.

        The result is cached. If for any reason the trajectory cannot
        be read then 0 is returned.
        """
        if not self.__numatoms is None:   # return cached value
            return self.__numatoms
        try:
            self.__numatoms = self._read_trj_natoms(self.filename)
        except IOError:
            return 0
        else:
            return self.__numatoms

    def _read_trj_natoms(self, filename):
        """Generic number-of-atoms extractor with minimum intelligence. Override if necessary."""
        if self.format == 'XTC':
            numatoms = libxdrfile.read_xtc_natoms(self.filename)
        elif self.format == 'TRR':
            numatoms = libxdrfile.read_trr_natoms(self.filename)
        else:
            raise NotImplementedError("Gromacs trajectory format %s not known." % self.format)
        return numatoms
        
    def open_trajectory(self):
        """Open xdr trajectory file.

        :Returns: pointer to XDRFILE (and sets self.xdrfile)
        :Raises:  :exc:`IOError` with code EALREADY if file was already opened or 
                  ENOENT if the file cannot be found
        """
        if not self.xdrfile is None:
            raise IOError(errno.EALREADY, 'XDR file already opened', self.filename)
        if not os.path.exists(self.filename):
            # must check; otherwise might segmentation fault
            raise IOError(errno.ENOENT, 'XDR file not found', self.filename)
        self.xdrfile = libxdrfile.xdrfile_open(self.filename, 'r')
        # reset ts
        ts = self.ts
        ts.status = libxdrfile.exdrOK
        ts.frame = 0
        ts.step = 0
        ts.time = 0
        # additional data for xtc
        ts.prec = 0
        # additional data for TRR
        ts.lmbda = 0
        
        return self.xdrfile

    def close_trajectory(self):
        """Close xdr trajectory file if it was open."""
        if self.xdrfile is None:
            return
        libxdrfile.xdrfile_close(self.xdrfile)
        self.xdrfile = None  # guard against  crashing with a double-free pointer

    def __iter__(self):
        self.ts.frame = 0  # start at 0 so that the first frame becomes 1
        self._reopen()
        while True:
            try:
                ts = self._read_next_timestep()
            except IOError, err:
                if err.errno == errno.ENODATA:
                    break
                else:
                    self.close_trajectory()
                    raise
            except:
                self.close_trajectory()
                raise
            else:
                yield ts

    def _read_next_timestep(self, ts=None):
        """Generic ts reader with minimum intelligence. Override if necessary."""
        if ts is None: 
            ts = self.ts
        if self.xdrfile is None:
            self.open_trajectory()

        if self.format == 'XTC':
            ts.status, ts.step, ts.time, ts.prec = libxdrfile.read_xtc(self.xdrfile, ts._unitcell, ts._pos)
        elif self.format == 'TRR':
            ts.status, ts.step, ts.time, ts.lmbda = libxdrfile.read_trr(self.xdrfile, ts._unitcell, ts._pos,
                                                                        ts._velocities, ts._forces)
        else:
            raise NotImplementedError("Gromacs trajectory format %s not known." % self.format)
        if (ts.status == libxdrfile.exdrENDOFFILE) or \
                (ts.status == libxdrfile.exdrINT and self.format == 'TRR'):
            # seems that trr files can get a exdrINT when reaching EOF (??)
            raise IOError(errno.ENODATA, "End of file reached for %s file" % self.format, 
                          self.filename)
        elif not ts.status == libxdrfile.exdrOK:
            raise IOError(errno.EFAULT, "Problem with %s file, status %s" % 
                          (self.format, statno.errorcode[ts.status]), self.filename)
        ts.frame += 1
        return ts

    def next(self):
        """Forward one step to next frame."""
        return self._read_next_timestep()

    def rewind(self):
        """Position at beginning of trajectory"""
        self._reopen()
        self.next()   # read first frame

    def _reopen(self):
        self.close_trajectory()
        self.open_trajectory()        

    def timeseries(self, asel, start=0, stop=-1, skip=1, format='afc'):
        raise NotImplementedError("timeseries not available for Gromacs trajectories")
    def correl(self, timeseries, start=0, stop=-1, skip=1):
        raise NotImplementedError("correl not available for Gromacs trajectories")

    def __del__(self):
        self.close_trajectory()
        

class TrjWriter(DCD.DCDWriter):
    """Writes to a Gromacs trajectory file
    
    (Base class)
    """
    format = None

    def __init__(self, filename, numatoms, start=0, step=1, delta=1.0, precision=1000.0, remarks=None):
        ''' Create a new TrjWriter
        filename - name of output file
        numatoms - number of atoms in trajectory file
        start - starting timestep
        step  - skip between subsequent timesteps
        delta - timestep
        precision - accuracy for lossy XTC format [1000]
        '''
        assert self.format in ('XTC', 'TRR')

        if numatoms == 0:
            raise ValueError("TrjWriter: no atoms in output trajectory")
        self.filename = filename
        self.numatoms = numatoms

        self.frames_written = 0
        self.start = start
        self.step = step
        self.delta = delta
        self.remarks = remarks
        self.precision = precision  # only for XTC

        self.xdrfile = libxdrfile.xdr_open(filename, 'w')
        self.ts = None

    def write_next_timestep(self, ts=None):
        ''' write a new timestep to the trj file
            ts - timestep object containing coordinates to be written to dcd file
        '''
        if self.xdrfile is None:
            raise IOError("Attempted to write to closed file %r", self.filename)
        if ts is None:
            if not hasattr(self, "ts"):
                raise IOError("TrjWriter: no coordinate data to write to trajectory file")
            else:
                ts=self.ts
        elif not ts.numatoms == self.numatoms:
            # Check to make sure Timestep has the correct number of atoms
            raise IOError("TrjWriter: Timestep does not have the correct number of atoms")

        status = self._write_next_timestep(ts)

        if status != libxdrfile.exdrOK:
            raise IOError(errno.EIO, "Error writing %s file (status %d)" % (self.format, status), self.filename)
        self.frames_written += 1

    def _write_next_timestep(self, ts):
        """Generic writer with minimum intelligence; override if necessary."""
        if self.format == 'XTC':
            status = libxdrfile.write_xtc(self.xdrfile, ts.step, ts.time, ts._unitcell, ts._pos, self.precision)
        elif self.format == 'TRR':
            status = libxdrfile.write_trr(self.xdrfile, ts.step, ts.time, ts.lmbda, ts._unitcell, 
                                           ts._pos, ts._velocities, ts._forces)
        else:
            raise NotImplementedError("Gromacs trajectory format %s not known." % self.format)
        return status

    def close_trajectory(self):
        status = libxdrfile.exdrCLOSE
        if not self.xdrfile is None:
            status = libxdrfile.xdrfile_close(self.xdrfile)
            self.xdrfile = None
        return status
    def __del__(self):
        self.close_trajectory()
    def __repr__(self):
        return "< TrjWriter '"+ self.filename + "' for " + repr(self.numatoms) + " atoms >"
