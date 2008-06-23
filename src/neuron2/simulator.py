from pyNN import __path__ as pyNN_path
from pyNN import common, recording
import platform
import logging
import numpy
import os.path
import neuron
h = neuron.h

# Global variables
nrn_dll_loaded = []

def load_mechanisms(path=pyNN_path[0]):
    global nrn_dll_loaded
    if path not in nrn_dll_loaded:
        arch_list = [platform.machine(), 'i686', 'x86_64', 'powerpc']
        # in case NEURON is assuming a different architecture to Python, we try multiple possibilities
        for arch in arch_list:
            lib_path = os.path.join(path, 'hoc', arch, '.libs', 'libnrnmech.so')
            if os.path.exists(lib_path):
                h.nrn_load_dll(lib_path)
                nrn_dll_loaded.append(path)
                return
        raise Exception("NEURON mechanisms not found in %s." % os.path.join(path, 'hoc'))

class Recorder(object):
    """Encapsulates data and functions related to recording model variables."""
    
    numpy_formats = {'spikes': "%g\t%d",
               'v': "%g\t%g\t%d"}
    formats = {'spikes': 't id',
               'v': 't v id'}
    
    def __init__(self, variable, population=None, file=None):
        """
        `file` should be one of:
            a file-name,
            `None` (write to a temporary file)
            `False` (write to memory).
        """
        self.variable = variable
        self.filename = file or None
        self.population = population # needed for writing header information
        self.recorded = set([])
        

    def record(self, ids):
        """Add the cells in `ids` to the set of recorded cells."""
        logging.debug('Recorder.record(%s)', str(ids))
        if self.population:
            ids = set([id for id in ids if id in self.population._local_ids])
        else:
            ids = set(ids) # how to decide if the cell is local?
        new_ids = list( ids.difference(self.recorded) )
        
        self.recorded = self.recorded.union(ids)
        logging.debug('Recorder.recorded = %s' % self.recorded)
        if self.variable == 'spikes':
            for id in new_ids:
                id._cell.record(1)
        elif self.variable == 'v':
            for id in new_ids:
                id._cell.record_v(1)
        
    def get(self, gather=False):
        """Returns the recorded data."""
        if self.variable == 'spikes':
            data = numpy.empty((0,2))
            for id in self.recorded:
                spikes = id._cell.spiketimes.toarray()
                spikes = spikes[spikes<=state.t+1e-9]
                if len(spikes) > 0:
                    new_data = numpy.array([spikes, numpy.ones(spikes.shape)*id]).T
                    data = numpy.concatenate((data, new_data))
        elif self.variable == 'v':
            data = numpy.empty((0,3))
            for id in self.recorded:
                v = id._cell.vtrace.toarray()
                t = id._cell.record_times.toarray()
                new_data = numpy.array([t, v, numpy.ones(v.shape)*id]).T
                data = numpy.concatenate((data, new_data))
        return data
    
    def write(self, file=None, gather=False, compatible_output=True):
        data = self.get(gather)
        filename = file or self.filename
        numpy.savetxt(filename, data, Recorder.numpy_formats[self.variable])
        if compatible_output:
            recording.write_compatible_output(filename, filename, Recorder.formats[self.variable],
                                              self.population, state.dt)
        
class _Initializer(object):
    
    def __init__(self):
        self.cell_list = []
        self.population_list = []
        h('objref initializer')
        neuron.h.initializer = self
        self.fih = h.FInitializeHandler("initializer.initialize()")
    
    def __call__(self):
        """This is to make the Initializer a Singleton."""
        return self
    
    def register(self, *items):
        for item in items:
            if isinstance(item, common.Population):
                if "Source" not in item.celltype.__class__.__name__: # don't do memb_init() on spike sources
                    self.population_list.append(item)
            else:
                if hasattr(item._cell, "memb_init"):
                    self.cell_list.append(item)
    
    def initialize(self):
        logging.info("Initializing membrane potential of %d cells and %d Populations." % \
                     (len(self.cell_list), len(self.population_list)))
        for cell in self.cell_list:
            cell._cell.memb_init()
        for population in self.population_list:
            for cell in population:
                cell._cell.memb_init()

def h_property(name):
    def _get(self):
        return getattr(h,name)
    def _set(self, val):
        setattr(h, name, val)
    return property(fget=_get, fset=_set)

class _State(object):
    """Represent the simulator state."""
    
    def __init__(self):
        self.gid_counter = 0
        self.running = False
        self.initialized = False
        h('min_delay = 0')
        h('tstop = 0')
        self.parallel_context = neuron.ParallelContext()
        self.parallel_context.spike_compress(1,0)
        self.num_processes = int(self.parallel_context.nhost())
        self.mpi_rank = int(self.parallel_context.id())
        self.cvode = neuron.CVode()
    
    t = h_property('t')
    dt = h_property('dt')
    tstop = h_property('tstop')         # } do these really need to be stored in hoc?
    min_delay = h_property('min_delay') # }
    
    
    def __call__(self):
        """This is to make the State a Singleton."""
        return self
    
def reset():
    state.running = False
    state.t = 0
    state.tstop = 0

def run(simtime):
    if not state.running:
        state.running = True
        local_minimum_delay = state.parallel_context.set_maxstep(10)
        h.finitialize()
        state.tstop = 0
        logging.debug("local_minimum_delay on host #%d = %g" % (state.mpi_rank, local_minimum_delay))
        if state.num_processes > 1:
            assert local_minimum_delay >= state.min_delay,\
                   "There are connections with delays (%g) shorter than the minimum delay (%g)" % (local_minimum_delay, state.min_delay)
    state.tstop = simtime
    logging.info("Running the simulation for %d ms" % simtime)
    state.parallel_context.psolve(state.tstop)
    return state.t


def finalize(quit=True):
    state.parallel_context.runworker()
    state.parallel_context.done()
    if quit:
        logging.info("Finishing up with NEURON.")
        h.quit()

def register_gid(gid, source):
    state.parallel_context.set_gid2node(gid, state.mpi_rank)  # assign the gid to this node
    nc = neuron.NetCon(source, None)                          # } associate the cell spike source
    state.parallel_context.cell(gid, nc.hoc_obj)              # } with the gid (using a temporary NetCon)

def nativeRNG_pick(n, rng, distribution='uniform', parameters=[0,1]):
    native_rng = h.Random(0 or rng.seed)
    rarr = [getattr(native_rng, distribution)(*parameters)]
    rarr.extend([native_rng.repick() for j in xrange(n-1)])
    return numpy.array(rarr)

class Connection(object):

    def __init__(self, source, target, nc):
        self.pre = source
        self.post = target
        self.nc = nc

def single_connect(source, target, weight, delay, synapse_type):
    """
    Private function to connect two neurons.
    Used by `connect()` and the `Connector` classes.
    """
    if not isinstance(source, int) or source > state.gid_counter or source < 0:
        errmsg = "Invalid source ID: %s (gid_counter=%d)" % (source, state.gid_counter)
        raise common.ConnectionError(errmsg)
    if not isinstance(target, common.IDMixin):
        raise common.ConnectionError("Invalid target ID: %s" % target)
    if synapse_type is None:
        synapse_type = weight>=0 and 'excitatory' or 'inhibitory'
    if weight is None:
        weight = common.DEFAULT_WEIGHT
    if "cond" in target.cellclass.__name__:
        weight = abs(weight) # weights must be positive for conductance-based synapses
    elif synapse_type == 'inhibitory' and weight > 0:
        weight *= -1         # and negative for inhibitory, current-based synapses
    if delay is None:
        delay = state.min_delay
    elif delay < state.min_delay:
        raise common.ConnectionError("delay (%s) is too small (< %s)" % (delay, state.min_delay))
    synapse_object = getattr(target._cell, synapse_type).hoc_obj
    nc = state.parallel_context.gid_connect(int(source), synapse_object)
    nc.weight[0] = weight
    nc.delay  = delay
    return Connection(source, target, nc)

# The following are executed every time the module is imported.
load_mechanisms() # maintains a list of mechanisms that have already been imported
state = _State()  # a Singleton, so only a single instance ever exists
initializer = _Initializer()