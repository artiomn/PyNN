"""
Support cell types defined in 9ML with NEST.

Requires the 9ml nestbuilder script to be on the import path.

Classes:
    NineMLCellType   - base class for cell types, not used directly

Functions:
    nineml_cell_type_from_model - return a new NineMLCellType subclass

Constants:
    NEST_DIR        - subdirectory to which NEST mechanisms will be written (TODO: not implemented)

:copyright: Copyright 2006-2011 by the PyNN team, see AUTHORS.
:license: CeCILL, see LICENSE for details.

"""

from __future__ import absolute_import # Not compatible with Python 2.4
import subprocess
import neuron
from pyNN.models import BaseCellType
#from pyNN.nineml.cells import _build_nineml_celltype
from pyNN.nineml.cells import CoBaSyn 
import logging
import os
from itertools import chain
#from pyNN.neuron import simulator
#from pyNN import common, recording
from pyNN.nest.cells import NativeCellType
#common.simulator = simulator
#recording.simulator = simulator



logger = logging.getLogger("PyNN")

# TODO: This should go to a evironment variable, like PYNN_9ML_DIR
# and then a sub-dir for nest, neuron, etc.
# but default to ~/.pyNN or something to that regard.
NEST_DIR = "nest_models"

class NineMLCellType(NativeCellType):
    
    def __init__(self, parameters):
        NativeCellType.__init__(self, parameters)


def nineml_celltype_from_model(name, nineml_model, synapse_components):
    """
    Return a new NineMLCellType subclass from a NineML model.
    """
    
    dct = {'nineml_model':nineml_model,
           'synapse_components':synapse_components}
    return _nest_build_nineml_celltype(name, (NineMLCellType,), dct)



class _nest_build_nineml_celltype(type):
    """
    Metaclass for building NineMLCellType subclasses
    Called by nineml_celltype_from_model
    """
    def __new__(cls, name, bases, dct):
        
        import nineml.abstraction_layer as nineml
        from nineml.abstraction_layer import models               
        import nest
        
        #Extract Parameters Back out from Dict:
        nineml_model = dct['nineml_model']
        synapse_components = dct['synapse_components']

        # Reduce the model:                    
        reduced_component = models.reduce_to_single_component( nineml_model,
                                                               componentname=name )
        reduced_component.backsub_all()
        #reduced_component.backsub_aliases()
        #reduced_component.backsub_equations()

        reduced_component.short_description = "Auto-generated 9ML neuron model for PyNN.nest"
        reduced_component.long_description = "Auto-generated 9ML neuron model for PyNN.nest"

        # synapse ports:
        synapse_ports = []
        for syn in synapse_components:
            # get recv event ports
            # TODO: model namespace look
            #syn_component = nineml_model[syn.namespace]
            syn_component = nineml_model.subnodes[syn.namespace]
            recv_event_ports = list(syn_component.filter_ports(cls=nineml.EventPort, mode='recv'))
            # check there's only one
            if len(recv_event_ports)!=1:
                raise ValueError, "A synapse component has multiple recv ports.  Cannot dis-ambiguate"
            synapse_ports.append(syn.namespace+'_'+recv_event_ports[0].symbol)

        
        # New:
        dct["combined_model"] = reduced_component
        # TODO: Override this with user layer
        #default_values = ModelToSingleComponentReducer.flatten_namespace_dict( parameters )
        dct["default_parameters"] = dict( (name, 1.0) for name in reduced_component.parameters )
        dct["default_initial_values"] = dict((name, 0.0) for name in reduced_component.state_variables)
        dct["synapse_types"] = [syn.namespace for syn in synapse_components] 
        dct["standard_receptor_type"] = (dct["synapse_types"] == ('excitatory', 'inhibitory'))
        dct["injectable"] = True # need to determine this. How??
        dct["conductance_based"] = True # how to determine this??
        dct["model_name"] = name
        dct["nest_model"] = name

        
        # Recording from bindings:
        dct["recordable"] = [port.name for port in reduced_component.analog_ports] + ['spikes', 'regime']
        # TODO bindings -> alias and support recording of them in nest template
        #+ [binding.name for binding in reduced_component.bindings]
        
        dct["weight_variables"] = dict([ (syn.namespace,syn.namespace+'_'+syn.weight_connector )
                                         for syn in synapse_components ])
        
        logger.debug("Creating class '%s' with bases %s and dictionary %s" % (name, bases, dct))

        # TODO: UL configuration of initial regime.
        initial_regime = reduced_component.regime_map.keys()[0]

        from nestbuilder import NestFileBuilder
        nfb = NestFileBuilder(  nest_classname = name, 
                                component = reduced_component, 
                                synapse_ports = synapse_ports,
                                initial_regime =  initial_regime,
                                initial_values = dct["default_initial_values"],
                                default_values = dct["default_parameters"],
                                )
        nfb.compile_files()
        nest.Install('mymodule')
        
        return type.__new__(cls, name, bases, dct)
        