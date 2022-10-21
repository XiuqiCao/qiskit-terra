from qiskit.circuit.quantumcircuit import QuantumCircuit
from qiskit.circuit.quantumregister import Qubit
from qiskit.transpiler.passes.paulihedral.block_ordering import do_ordering, gco_ordering
from qiskit.transpiler.passes.paulihedral.block_compilation import opt_ft_backend, opt_sc_backend
from qiskit.transpiler.passes.layout.noise_adaptive_layout import NoiseAdaptiveLayout


import sys


class Paulihedral:
    def __init__(
        self,
        inputprogram=None,
        ordering_method=None,
        backend_method=None,
        coupling_map=None,
        props=None,
    ):
        self.pauliIRprogram = inputprogram

        if ordering_method in ("gco", "gate-count-oriented"):
            self.ordering_method = gco_ordering
        elif ordering_method in ("do", "depth-oriented"):
            self.ordering_method = do_ordering
        else:
            """need to raise some error"""
            print("unknown ordering method")

        if backend_method == "ft" or backend_method == "fault-tolerant":
            self.block_opt_method = opt_ft_backend
        elif backend_method == "sc" or backend_method == "superconducting":
            self.block_opt_method = opt_sc_backend
            if props == None:
                print(
                    "raise error: there is no information about backend "
                    "properties which is required in superconducting backend."
                )
            else:
                self.coupling_map = coupling_map
        else:
            """need to raise some error"""
            print("backend not support")
        self.output_circuit = None
        self.adj_mat = None
        self.dist_mat = None
        """
        get the adj_mat and dist_mat
        If adj_mat[i][j] is 0, then i=j or we can't apply a CNOT or SWAP gate between qubit i and j.
        If adj_mat[i][j] is 1, can apply a CNOT or SWAP gate between qubit i and j.
        dist_mat[i][j] equals 1 - error_rate(to SWAP i and j)
        """
        if props != None:
            noise_res = NoiseAdaptiveLayout(props)
            noise_res._initialize_backend_prop()
            gate_reliability = noise_res.gate_reliability
            swap_reliabs = noise_res.swap_reliabs
            qubit_num = len(swap_reliabs)
            self.adj_mat = []
            for i in range(qubit_num):
                self.adj_mat.append([])
                for j in range(qubit_num):
                    self.adj_mat[i].append(0)
            self.dist_mat = []
            for i in range(qubit_num):
                self.dist_mat.append([])
                for j in range(qubit_num):
                    self.dist_mat[i].append(0)
            for cx in gate_reliability.keys():
                i = int(cx[0])
                j = int(cx[1])
                (self.adj_mat)[i][j] = 1
                (self.adj_mat)[j][i] = 1
            for key1, value1 in swap_reliabs.items():
                for key2, value2 in value1.items():
                    self.dist_mat[key1][key2] = 1.0 - value2

    def load_from_qubit_op(self, qubit_op_list, evolution_time_step):
        pauliIRprogram = []
        for op in qubit_op_list:
            pauliIRprogram.append([[op.primitive.to_label(), op.coeff], evolution_time_step])
        return pauliIRprogram

    def load_from_UCCSD(self, UCCSD_instance, parameters):
        pauliIRprogram = []
        for i, parameter_block in enumerate(UCCSD_instance._hopping_ops):
            pauli_block = []
            parameter_block = parameter_block.to_dict()["paulis"]
            for j in parameter_block:
                pauli_block.append([j["label"], j["coeff"]["imag"]])
            pauli_block.append(parameters[i])
            pauliIRprogram.append(pauli_block)
        return pauliIRprogram

    def opt(self, do_max_iteration=30):
        self.pauli_layers = self.ordering_method(self.pauliIRprogram, do_max_iteration)
        self.output_circuit = self.block_opt_method(self.pauli_layers, self.adj_mat, self.dist_mat)
        return self.output_circuit

    def to_circuit(self):
        if self.output_circuit == None:
            self.output_circuit = self.opt()
        return self.output_circuit


from qiskit.providers.backend import Backend
from qiskit.providers.models import BackendProperties
from qiskit.providers.models.backendproperties import Gate
from typing import List, Union, Dict, Callable, Any, Optional, Tuple, Iterable
from qiskit import QuantumRegister, ClassicalRegister, QuantumCircuit
from qiskit.dagcircuit import DAGCircuit
from qiskit.converters import circuit_to_dag, dag_to_circuit
from qiskit.visualization import dag_drawer
from qiskit.compiler import transpile
import copy
from qiskit.transpiler.passes.layout.dense_layout import *
from qiskit.transpiler.coupling import CouplingMap


def get_layout(
    backend: Backend,
    layout_pre: Optional[Layout],
    layout_next: Optional[Layout],
    used_phy_qubits: list,
    used_virtual_qubits: list,
    qubit_num: int,
) -> Layout:
    """
    This function is used to find a good layout between used_phy_qubits and used_virtual_qubits
    in a heuristic algorithm. The purpose is to reduce the sum of operations of the transformation
    from the layout_pre to the output layout and from the output layout to the layout_next.
    The part of the circuit which will use the output layout is a PauliEvolutionKernel on some qubits
    and identity operations on other qubits.
    :param backend:
        The backend of the hardware
    :param layout_pre:
        The layout just before the circuit which will use the output layout.
    :param layout_next:
        The layout just after the circuit which will use the output layout.
    :param used_phy_qubits:
        The list contains all indexs of physical qubits which are used in the PauliEvolutionKernel.
    :param used_virtual_qubits:
        The list contains all indexs of virtual qubits which are used in the PauliEvolutionKernel.
    :param qubit_num:
        The qubit num of the input circuit(logical circuit) in the function Paulihedral_on_mixed_circuit().

    :return:
        The output layout with only registers named 'q' and without registers named 'ancilla'.
    """
    noise_res = NoiseAdaptiveLayout(backend.properties())
    noise_res._initialize_backend_prop()
    swap_reliabs = noise_res.swap_reliabs
    total_qubit_num = len(swap_reliabs)
    del swap_reliabs
    dist_mat = Backend_Processor(backend=backend).get_local_swap_reliab(range(total_qubit_num))
    for i in range(len(dist_mat)):
        for j in range(len(dist_mat)):
            dist_mat[i][j] = 1.0 - dist_mat[i][j]

    unused_phy_qubits = []
    for idx in range(total_qubit_num):
        unused_phy_qubits.append(idx)
    for qubit_idx in used_phy_qubits:
        unused_phy_qubits.remove(qubit_idx)
    unused_virtual_qubits = []
    for idx in range(total_qubit_num):
        unused_virtual_qubits.append(idx)
    for qubit_idx in used_virtual_qubits:
        unused_virtual_qubits.remove(qubit_idx)

    layout = Layout()

    """
       «─used_virtual_qubits─»   «───unused_virtual_qubits───»
       «───used_phy_qubits───»   «─unused_phy_qubits─»
        q   q   q   q   q   q    q   q   q   q   q   q   a   a   (a: short for ancilla)
        │   │   │   │   │   │    │   │   │   │   │   │   │   │   'q' or 'ancilla' is the name of the
      ┌─┴───┴───┴───┴───┴───┴─┐  │   │   │   │   │   │   │   │   register
      │ PauliEvolutionKernel  │  │   │   │   │   │   │   │   │   
      └─┬───┬───┬───┬───┬───┬─┘  │   │   │   │   │   │   │   │   
        │   │   │   │   │   │    │   │   │   │   │   │   │   │  
       «──────────────────qubit_num──────────────────» 
       «───────────────────total_qubit_num───────────────────» 
    
        A figure to show the definition of used_virtual_qubits, unused_virtual_qubits, 
        used_phy_qubits, unused_phy_qubits, qubit_num, total_qubit_num.
    """

    # Case 0
    # We didn't have more detail about the layout_pre and the layout_next.
    if layout_pre == None and layout_next == None:
        qreg = QuantumCircuit(qubit_num).qregs[0]
        for i in range(len(used_phy_qubits)):
            layout[qreg[used_virtual_qubits[i]]] = int(used_phy_qubits[i])
        for i in range(qubit_num - len(used_phy_qubits)):
            layout[qreg[unused_virtual_qubits[i]]] = int(unused_phy_qubits[i])
        layout.add_register(qreg)
        return layout

    # Case 1
    # We didn't have more detail about the layout_pre.
    elif layout_pre == None and layout_next != None:
        input_dict = copy.deepcopy(layout_next._p2v)
        idx_to_delete = []
        for idx, qubit in input_dict.items():
            if qubit.register.name != "q":
                idx_to_delete.append(idx)
        for idx in idx_to_delete:
            del input_dict[idx]
        """
        input_dict(e.g.)
        {16: Qubit(QuantumRegister(7, 'q'), 1),
        11: Qubit(QuantumRegister(7, 'q'), 5),
        17: Qubit(QuantumRegister(7, 'q'), 3),
        12: Qubit(QuantumRegister(7, 'q'), 6),
        4: Qubit(QuantumRegister(7, 'q'), 0),
        5: Qubit(QuantumRegister(7, 'q'), 2),
        6: Qubit(QuantumRegister(7, 'q'), 4)}
        """
        qreg = QuantumCircuit(qubit_num).qregs[0]
        for idx in range(len(used_phy_qubits)):
            layout[qreg[used_virtual_qubits[idx]]] = int(used_phy_qubits[idx])  ###

        parallel_phy_qubits = []
        for idx in range(total_qubit_num):
            parallel_phy_qubits.append(idx)
        for idx in used_phy_qubits:
            parallel_phy_qubits.remove(idx)  ###

        parallel_virtual_qubits = []
        for idx, qubit in input_dict.items():
            parallel_virtual_qubits.append(qubit.index)
        for idx in used_virtual_qubits:
            parallel_virtual_qubits.remove(idx)  ###
        for virtual_qubit in parallel_virtual_qubits:
            phy_qubit_in_layout = None
            for idx, qubit in input_dict.items():
                if qubit.index == virtual_qubit:
                    phy_qubit_in_layout = idx
                    break
            """phy_qubit_in_layout: the physical qubit which matches the virtual_qubit in layout_next"""
            min_dist = 99.99
            phy_qubit_for_min_dist = None
            for phy_qubit in parallel_phy_qubits:
                if dist_mat[phy_qubit][phy_qubit_in_layout] < min_dist:
                    min_dist = dist_mat[phy_qubit][phy_qubit_in_layout]
                    phy_qubit_for_min_dist = phy_qubit
            layout[qreg[virtual_qubit]] = int(phy_qubit_for_min_dist)
            parallel_phy_qubits.remove(phy_qubit_for_min_dist)
        layout.add_register(qreg)
        return layout

    # Case 2
    # We didn't have more detail about the layout_next.
    elif layout_pre != None and layout_next == None:
        input_dict = copy.deepcopy(layout_pre._p2v)
        idx_to_delete = []
        for idx, qubit in input_dict.items():
            if qubit.register.name != "q":
                idx_to_delete.append(idx)
        for idx in idx_to_delete:
            del input_dict[idx]

        qreg = QuantumCircuit(qubit_num).qregs[0]
        for idx in range(len(used_phy_qubits)):
            layout[qreg[used_virtual_qubits[idx]]] = int(used_phy_qubits[idx])

        parallel_phy_qubits = []
        for idx in range(total_qubit_num):
            parallel_phy_qubits.append(idx)
        for idx in used_phy_qubits:
            parallel_phy_qubits.remove(idx)
        parallel_virtual_qubits = []
        for idx, qubit in input_dict.items():
            parallel_virtual_qubits.append(qubit.index)
        for idx in used_virtual_qubits:
            parallel_virtual_qubits.remove(idx)

        for virtual_qubit in parallel_virtual_qubits:
            phy_qubit_in_layout = None
            for idx, qubit in input_dict.items():
                if qubit.index == virtual_qubit:
                    phy_qubit_in_layout = idx
                    break
            """phy_qubit_in_layout: the physical qubit which match the virtual_qubit in layout_next"""
            min_dist = 99.99
            phy_qubit_for_min_dist = None
            for phy_qubit in parallel_phy_qubits:
                if dist_mat[phy_qubit][phy_qubit_in_layout] < min_dist:
                    min_dist = dist_mat[phy_qubit][phy_qubit_in_layout]
                    phy_qubit_for_min_dist = phy_qubit
            layout[qreg[virtual_qubit]] = int(phy_qubit_for_min_dist)
            parallel_phy_qubits.remove(phy_qubit_for_min_dist)
        layout.add_register(qreg)

    # Case 3
    elif layout_pre != None and layout_next != None:
        input_dict_pre = copy.deepcopy(layout_pre._p2v)
        input_dict_next = copy.deepcopy(layout_next._p2v)
        idx_to_delete = []
        for idx, qubit in input_dict_pre.items():
            if qubit.register.name != "q":
                idx_to_delete.append(idx)
        for idx in idx_to_delete:
            del input_dict_pre[idx]

        idx_to_delete = []
        for idx, qubit in input_dict_next.items():
            if qubit.register.name != "q":
                del input_dict_next[idx]
        for idx in idx_to_delete:
            del input_dict_next[idx]

        qreg = QuantumCircuit(qubit_num).qregs[0]
        for idx in range(len(used_phy_qubits)):
            layout[qreg[used_virtual_qubits[idx]]] = int(used_phy_qubits[idx])

        parallel_phy_qubits = []
        for idx in range(total_qubit_num):
            parallel_phy_qubits.append(idx)
        for idx in used_phy_qubits:
            parallel_phy_qubits.remove(idx)

        parallel_virtual_qubits = []
        for idx, qubit in input_dict_pre.items():
            parallel_virtual_qubits.append(qubit.index)
        for idx in used_virtual_qubits:
            parallel_virtual_qubits.remove(idx)

        for virtual_qubit in parallel_virtual_qubits:
            phy_qubit_in_layout_pre = None
            for idx, qubit in input_dict_pre.items():
                if qubit.index == virtual_qubit:
                    phy_qubit_in_layout_pre = idx
                    break
            phy_qubit_in_layout_next = None
            for idx, qubit in input_dict_pre.items():
                if qubit.index == virtual_qubit:
                    phy_qubit_in_layout_next = idx
                    break
            min_dist = 99.99
            phy_qubit_for_min_dist = None
            for phy_qubit in parallel_phy_qubits:
                if (
                    1.0
                    - (1.0 - dist_mat[phy_qubit][phy_qubit_in_layout_pre])
                    * (1.0 - dist_mat[phy_qubit][phy_qubit_in_layout_next])
                    < min_dist
                ):
                    min_dist = 1.0 - (1.0 - dist_mat[phy_qubit][phy_qubit_in_layout_pre]) * (
                        1.0 - dist_mat[phy_qubit][phy_qubit_in_layout_next]
                    )
                    phy_qubit_for_min_dist = phy_qubit
            layout[qreg[virtual_qubit]] = int(phy_qubit_for_min_dist)
            parallel_phy_qubits.remove(phy_qubit_for_min_dist)
        layout.add_register(qreg)
        return layout
    return layout


"""
def fill_layout_with_ancilla(
    backend: Backend, layout_without_ancilla: Layout, used_phy_qubits: list, qubit_num: int
) -> Layout:
    noise_res = NoiseAdaptiveLayout(backend.properties())
    noise_res._initialize_backend_prop()
    swap_reliabs = noise_res.swap_reliabs
    total_qubit_num = len(swap_reliabs)
    unused_phy_qubits = []
    for idx in range(total_qubit_num):
        unused_phy_qubits.append(idx)
    for qubit_idx in used_phy_qubits:
        unused_phy_qubits.remove(qubit_idx)
    layout = layout_without_ancilla
    qreg = QuantumCircuit(total_qubit_num - qubit_num).qregs[0]
    for i in range(total_qubit_num - qubit_num):
        layout[qreg[i]] = int(unused_phy_qubits[i])

    pass
"""

from qiskit.transpiler.passes.layout.apply_layout import ApplyLayout
from qiskit.transpiler.passes.layout.full_ancilla_allocation import FullAncillaAllocation
from qiskit.transpiler.passes.layout.enlarge_with_ancilla import EnlargeWithAncilla
from qiskit.transpiler.passes.routing import LayoutTransformation


def new_block_list_printing(new_block_list: List):
    """
    ATTENTION!!!
    ATTENTION!!!
    This function is only for debugging!!!
    for new_block_list in function_list
    if Kernel:
        layer= [ initial QuantumCircuit, True,(2) layout_without_ancilla, physical QuantumCircuit,
                used_phy_qubits_list, used_virtual_qubits_list]
    if not Kernel:
        layer = [ initial QuantumCircuit, False, layout without ancilla, QuantumCircuit after transpile ]
    """
    print("-------PRINTING-------")
    for idx, layer in enumerate(new_block_list):
        if layer[1] == True:
            print("The layer with number", idx)
            initial_QuantumCircuit = layer[0]
            is_Kernel = layer[1]
            layout_without_ancilla = layer[2]
            physical_QuantumCircuit = layer[3]
            used_phy_qubits_list, used_virtual_qubits_list = layer[4], layer[5]
            initial_QuantumCircuit._layout = None
            print("initial_QuantumCircuit\n", initial_QuantumCircuit)
            print("is_Kernel\n", is_Kernel)
            print("layout_without_ancilla\n", layout_without_ancilla)
            print("physical_QuantumCircuit\n", physical_QuantumCircuit)
            print("used_phy_qubits_list\n", used_phy_qubits_list)
            print("used_virtual_qubits_list\n", used_virtual_qubits_list)
        elif layer[1] == False:
            initial_QuantumCircuit = layer[0]
            is_Kernel = layer[1]
            layout_without_ancilla = layer[2]
            physical_QuantumCircuit = layer[3]
            print("The layer with number", idx)
            print("initial_QuantumCircuit\n", initial_QuantumCircuit)
            print("is_Kernel\n", is_Kernel)
            print("layout_without_ancilla\n", layout_without_ancilla)
            print("physical_QuantumCircuit\n", physical_QuantumCircuit)
            print("used_phy_qubits_list\n", used_phy_qubits_list)
            print("used_virtual_qubits_list\n", used_virtual_qubits_list)


from qiskit.providers.backend import Backend
from qiskit.transpiler.exceptions import *
import math


class Backend_Processor:
    def __init__(self, backend: Backend):
        """
        :param backend:
            The backend of the hardware.
        This class is used to process the backend and get some conclusion of it.
        """
        self.backend = backend
        self.prop_dict = backend.properties().to_dict()

    def get_local_swap_reliab(self, input_qubits_list: List):
        """
        :param input_qubits_list:
            A list whose element is all in type int.
            It stores all qubits which construct a local part.
            Warning: make sure that the element of the list is in order, from small to big.
        :return:
            A matrix.
            Its (i,j) element ([i][j]) stores a float number which represents the reliability
            of the SWAP operation between the physical qubit with number qubits_list[i] and
            qubit_list[j] in the backend hardware.
            In the operations, only the CNOT or SWAP gates between the qubits with number in
            qubit_list can be used.
        """
        properties_gates_list = self.prop_dict["gates"]

        properties_cx_gates_list = []
        """ it element e.g. {'qubits': [7, 6],'gate_error': 0.03754135340392492}"""
        for gates in properties_gates_list:
            if gates["gate"] == "cx":
                if (
                    gates["qubits"][0] in input_qubits_list
                    and gates["qubits"][1] in input_qubits_list
                ):
                    dict = {}
                    dict["qubits"] = gates["qubits"]
                    dict["gate_error"] = gates["parameters"][0]["value"]
                    dict["weight"] = -3.0 * math.log(1 - gates["parameters"][0]["value"])
                    properties_cx_gates_list.append(dict)
        dict_idx_to_phynum = {}
        dict_phynum_to_idx = {}
        for idx, val in enumerate(input_qubits_list):
            dict_idx_to_phynum[idx] = val
            dict_phynum_to_idx[val] = idx
        for cx_gate in properties_cx_gates_list:
            cx_gate["qubits"][0] = dict_phynum_to_idx[cx_gate["qubits"][0]]
            cx_gate["qubits"][1] = dict_phynum_to_idx[cx_gate["qubits"][1]]
        """Floyd Algorithm"""
        result = []
        for i in range(len(input_qubits_list)):
            df = []
            for j in range(len(input_qubits_list)):
                df.append(0.0)
            result.append(df)

        for i in range(len(input_qubits_list)):
            for j in range(len(input_qubits_list)):
                if i == j:
                    result[i][j] = {"length": 0.0, "pre": i}
                elif i != j:
                    result[i][j] = {"length": 99999999.9, "pre": -1}
        for v in range(len(input_qubits_list)):
            for cx_gate in properties_cx_gates_list:
                """e.g. for cx_gate  {'qubits': [0, 2], 'gate_error': 0.037493190769214924, 'weight': 0.11464241495919317}"""
                if cx_gate["qubits"][0] == v:
                    result[v][cx_gate["qubits"][1]]["length"] = cx_gate["weight"]
                    result[v][cx_gate["qubits"][1]]["pre"] = v

        reliability_mat = copy.deepcopy(result)
        for i in range(len(input_qubits_list)):
            for j in range(len(input_qubits_list)):
                reliability_mat[i][j] = math.exp(-result[i][j]["length"])

        for v in range(len(input_qubits_list)):
            for i in range(len(input_qubits_list)):
                for j in range(len(input_qubits_list)):
                    if result[i][j]["length"] > result[i][v]["length"] + result[v][j]["length"]:
                        result[i][j]["length"] = result[i][v]["length"] + result[v][j]["length"]
                        result[i][j]["pre"] = result[v][j]["pre"]

        reliability_mat = copy.deepcopy(result)
        for i in range(len(input_qubits_list)):
            for j in range(len(input_qubits_list)):
                reliability_mat[i][j] = math.exp(-result[i][j]["length"])
        return reliability_mat

    def get_local_adjacent_mat(self, input_qubits_list: List):
        """
        :param input_qubits_list:
            A list whose element is all in type int.
            It stores all qubits which construct a local part.
            Warning: make sure that the element of the list is in order, from small to big.
        :return:
            A matrix.
            Its (i,j) element ([i][j]) stores 1 if the CNOT or SWAP gate can be used between
            qubit i and j.
            In the operations, only the CNOT or SWAP gates between the qubits with number in
            qubit_list can be used.
        """
        properties_gates_list = self.prop_dict["gates"]

        cx_gates_list = []
        """ {'qubits': [7, 6],'gate_error': 0.03754135340392492}"""
        for gates in properties_gates_list:
            if gates["gate"] == "cx":
                if (
                    gates["qubits"][0] in input_qubits_list
                    and gates["qubits"][1] in input_qubits_list
                ):
                    cx_gates_list.append([gates["qubits"][0], gates["qubits"][1]])
        dict_idx_to_phynum = {}
        dict_phynum_to_idx = {}
        for idx, val in enumerate(input_qubits_list):
            dict_idx_to_phynum[idx] = val
            dict_phynum_to_idx[val] = idx
        for pair in cx_gates_list:
            pair[0] = dict_phynum_to_idx[pair[0]]
            pair[1] = dict_phynum_to_idx[pair[1]]
        adj_mat = []
        for i in range(len(input_qubits_list)):
            line = []
            for j in range(len(input_qubits_list)):
                line.append(0)
            adj_mat.append(line)

        for i in range(len(input_qubits_list)):
            for j in range(len(input_qubits_list)):
                is_in = False
                for cx_gate in cx_gates_list:
                    if cx_gate[0] == i and cx_gate[1] == j:
                        is_in = True
                if is_in == True:
                    adj_mat[i][j] = 1

        return adj_mat

    def get_coupling_map(self) -> CouplingMap:
        properties_gates_list = self.prop_dict["gates"]
        cx_gates_list = []
        """e.g. for elements in cx_gates_list: {'qubits': [7, 6],'gate_error': 0.03754135340392492}"""
        for gates in properties_gates_list:
            if gates["gate"] == "cx":
                cx_gates_list.append([gates["qubits"][0], gates["qubits"][1]])
        return CouplingMap(cx_gates_list)


def Paulihedral_on_different_qubits_num(
    input_circuit: QuantumCircuit,
    global_backend: Backend,
    global_coupling_map: CouplingMap,
    ordering_method=None,
    backend_method=None,
    do_max_iteration=30,
    used_phy_qubits_list=[],
) -> QuantumCircuit:
    """
    :param input_circuit:
        An logical circuit represents the relationship between each logical qubits.
    :param global_backend:
        The backend of the quantum computer we will working on.
    :param global_coupling_map:
        The coupling map of the quantum computer we will working on.
    :param ordering_method:
        The ordering_method of Paulihedral for the PauliEvolutionKernels in the Circuit.
        You can choose 'gate-count-oriented' or 'depth-oriented'.
    :param backend_method:
        The backend_method of Paulihedral for the PauliEvolutionKernels in the Circuit.
        You can choose 'fault-tolerant' or 'superconducting'.
    :param do_max_iteration:
        The do_max_iteration of Paulihedral for the PauliEvolutionKernels in the Circuit.
    :param used_phy_qubits_list:
        The list stores indexes of the qubits which are used but not ancilla.
        !!!The list not only records the indexes of qubits which is used in the PauliEvolutionKernel,
        but also records the qubits which will be used in the in

    :return:
        The physical quantum circuit which has equivalent operation to the input_circuit.
        It is able to be achieved in the backend hardware and it is after optimization.

    The process of this function is on the following steps.
    1, Divide the input logical circuit into layers.
        Each layer is of QuantumCircuit class which has the same qubits number compare to the input circuit.
        It has either a pauli evolutional kernel or several normal gates (gates except pauli evolutional kernel).
        If we linked all the layers together in order, we will obtain a logical circuit which is equal to the
        input logical circuit.
    2, Handle each layer and get its layout accroding to its detail
        If it has a pauli evolutional kernel, we will use the class Paulihedral to optimizatize it. And if it
        is made of basic gates, it will use the function qiskit.compiler.transpile to optimizatize it.
    3, Connect all the layer together and transform the layout between two adjacent layers
        In the previous step, we also get the layout between the logical qubits of the input circuit to the
        physical qubits of the backend of each layer. In this step, we will connect these layers together. In
        the connected point, we will use the class qiskit.transpiler.pass.routing.LayoutTransformation to connect
        2 circuits which have different layout.
    """
    phy_dag = circuit_to_dag(input_circuit)
    layout = Layout()
    kernel_qc = None
    for node in phy_dag.op_nodes():
        if node.name == "PauliEvolutionKernel":
            """type(node.op)): <class 'qiskit.circuit.library.pauli_evolution.PauliEvolutionKernel'>"""
            kernel_qc = node.op
            qubit_num_on_Kernel = len(node.qargs)
            qreg = QuantumCircuit(qubit_num_on_Kernel).qregs[0]
            for idx, qubit in enumerate(list(node.qargs)):
                layout[qreg[idx]] = int(qubit.index)

    if ordering_method == "gco" or ordering_method == "gate-count-oriented":
        pauli_layers = gco_ordering(kernel_qc, do_max_iteration)
    elif ordering_method == "do" or ordering_method == "depth-oriented":
        pauli_layers = do_ordering(kernel_qc, do_max_iteration)
    else:
        """need to raise some error"""
        print("unknown ordering method")

    if backend_method == "ft" or backend_method == "fault-tolerant":
        output_circuit = opt_ft_backend(pauli_layers, adj_mat=[], dist_mat=[])
    elif backend_method == "sc" or backend_method == "superconducting":
        adj_mat = Backend_Processor(backend=global_backend).get_local_adjacent_mat(
            used_phy_qubits_list
        )
        dist_mat = Backend_Processor(backend=global_backend).get_local_swap_reliab(
            used_phy_qubits_list
        )
        for i in range(len(dist_mat)):
            for j in range(len(dist_mat)):
                dist_mat[i][j] = 1.0 - dist_mat[i][j]

        output_circuit = opt_sc_backend(pauli_layers, adj_mat=adj_mat, dist_mat=dist_mat)

    output_circuit._layout = None
    """get physical QuantumCircuit (without layout) but Kernels folded"""
    full_ancilla_pass = FullAncillaAllocation(coupling_map=global_coupling_map)
    full_ancilla_pass.property_set["layout"] = copy.deepcopy(layout)
    qc_temp = copy.deepcopy(output_circuit)
    qc_temp._layout = None
    # print('254\n',qc_temp,'\n\n',layout)
    """
     Layout({
    1: Qubit(QuantumRegister(4, 'q'), 0),
    4: Qubit(QuantumRegister(4, 'q'), 1),
    2: Qubit(QuantumRegister(4, 'q'), 2),
    5: Qubit(QuantumRegister(4, 'q'), 3)
    })
    """
    phy_dag = full_ancilla_pass.run(circuit_to_dag(qc_temp))  # bug
    layout = full_ancilla_pass.property_set["layout"]
    apply_layout_pass = ApplyLayout()
    apply_layout_pass.property_set["layout"] = layout
    phy_dag = apply_layout_pass.run(phy_dag)
    phy_circ = dag_to_circuit(phy_dag)
    phy_circ._layout = None
    return phy_circ


def Paulihedral_on_mixed_circuit(
    qc: QuantumCircuit,
    backend: Optional[Backend] = None,
    coupling_map: CouplingMap = None,
    ordering_method=None,
    backend_method=None,
    do_max_iteration: int = 30,
):
    """
    :param qc:
        The input logical QuantumCircuit.
    :param backend:
        The hardware backend used on the optimization.
    :param coupling_map:
        The CouplingMap of the physical qubits of the hardware.
    :param ordering_method:
        The ordering_method of the class Paulihedral.
        You can choose 'gco' ('gate-count-oriented') or 'do' ('depth-oriented').
    :param backend_method:
        The backend_method of the class Paulihedral.
        You can choose 'ft' ('fault-tolerant') or 'sc' ('superconducting').
    :param do_max_iteration:
        A super parameter in Paulihedral.
    :return:
        The physical Quantum Circuit with layout after the Paulihedral optimization for
        PauliEvolutionKernel and the regular optimization for the rest.
    """
    if backend == None:
        raise TranspilerError(f"backend is in need in function Paulihedral_on_mixed_circuit()")
        return None
    if ordering_method == None:
        raise TranspilerError(
            f"ordering_method is in need in function Paulihedral_on_mixed_circuit()"
        )
        return None
    if backend_method == None:
        raise TranspilerError(
            f"backend_method is in need in function Paulihedral_on_mixed_circuit()"
        )
        return None
    if coupling_map == None:
        backend_processor = Backend_Processor(backend=backend)
        coupling_map = backend_processor.get_coupling_map()

    """
    Next we will store the information of qc into block_list. In the process, the QuantumCircuit will spilt into layers.
    """
    input_dag = circuit_to_dag(qc)
    layers = input_dag.layers()
    block_list = []
    while True:
        a = next(layers, None)
        if a == None:
            break
        circ = dag_to_circuit(a["graph"])
        has_Kernel = False
        num_Kernel = 0
        for node in a["graph"].op_nodes():
            if str(node.op.name) == "PauliEvolutionKernel":
                has_Kernel = True
                num_Kernel += 1

        if has_Kernel == False:
            block_list.append([circ, has_Kernel])
        elif has_Kernel == True:
            dag1 = copy.deepcopy(a["graph"])
            dag2 = copy.deepcopy(a["graph"])
            dag2_list = []
            for node in dag1.op_nodes():
                if str(node.op.name) == "PauliEvolutionKernel":
                    dag1.remove_op_node(node)
            for node in dag2.op_nodes():
                if str(node.op.name) != "PauliEvolutionKernel":
                    dag2.remove_op_node(node)
            for node in dag2.op_nodes():
                if str(node.op.name) == "PauliEvolutionKernel":
                    node.name = "PauliEvolutionKernel_sign"
                    dag2_copy = copy.deepcopy(dag2)
                    dag2_copy.remove_all_ops_named("PauliEvolutionKernel")
                    for node_copy in dag2_copy.op_nodes():
                        node_copy.name = "PauliEvolutionKernel"
                    dag2_list.append(dag2_copy)
                    node.name = "PauliEvolutionKernel"
            if len(dag1.op_nodes()) > 0:
                block_list.append([dag_to_circuit(dag1), False])
            for idx, kernel in enumerate(dag2_list):
                block_list.append([dag_to_circuit(kernel), True])
    """
    In the stage, the information is stored in block_list:List.
    Every element of the block_list represents onr layer of the logical dag.
    For a single element,
        [circuit of the layer, True for PauliEvolutionKernel or False for other]
    In addition, there will be no element contains 2 or more PauliEvolutionKernel
    """

    if block_list == []:
        raise TranspilerError(f"empty input QuantumCircuit")
        return None
    qubit_num = qc.qregs[0]._size
    if block_list[0][1] == False:
        new_block_list = [[block_list[0][0], False]]
    elif block_list[0][1] == True:
        new_block_list = [[block_list[0][0], True]]

    for idx, layer in enumerate(block_list):
        if idx == 0:
            continue
        if layer[1] == False and new_block_list[-1][1] == False:
            new_block_list[-1][0].extend(layer[0])
        elif layer[1] == False and new_block_list[-1][1] == True:
            new_block_list.append([layer[0], False])
        elif layer[1] == True:
            new_block_list.append([layer[0], True])
    del block_list

    """
    Now the information is stored in new_block_list:List.
    For a single element in new_block_list,
        layer=[ initial QuantumCircuit(only logical), True or False]
    Next, we will get the used_phy_qubits_list, used_virtual_qubits_list for layers.
        used_phy_qubits_list:
            A list which stores every index of the physical qubits which will be used for Kernel.
        used_virtual_qubits_list:
            A list which stores every index of the virtual qubits which will be used for Kernel.
            In addition, the virtual qubits is as same as the qubits in the input qc in function
            Paulihedral_on_mixed_circuit().
    """

    dense_layout_pass = DenseLayout(coupling_map=coupling_map, backend_prop=backend.properties())
    for layer in new_block_list:
        if layer[1] == True:
            virtual_qubits_used = []
            node = circuit_to_dag(layer[0]).op_nodes()[0]
            for qubit in node.qargs:
                virtual_qubits_used.append(qubit.index)
            best_subset = dense_layout_pass._best_subset(
                num_qubits=len(virtual_qubits_used), num_meas=0, num_cx=0
            )
            layer.append(None)
            layer.append(None)
            layer.append(best_subset)
            layer.append(virtual_qubits_used)
        elif layer[1] == False:
            new_layer = transpile(circuits=layer[0], backend=backend, optimization_level=3)
            idx_to_delete = []
            layout_1 = copy.deepcopy(new_layer._layout)
            for phy_idx, qubit in layout_1._p2v.items():
                if qubit._register._name == "ancilla":
                    idx_to_delete.append(phy_idx)
            for idx in idx_to_delete:
                del layout_1._v2p[layout_1._p2v[idx]]
                del layout_1._p2v[idx]
            layer.append(layout_1)
            layer.append(copy.deepcopy(new_layer))

    """
    Now in new_block_list:
        if Kernel:
            layer= [ initial QuantumCircuit, True, None, None, used_phy_qubits_list, used_virtual_qubits_list]
        if not Kernel:
            layer= [ initial QuantumCircuit, False, layout without ancilla, QuantumCircuit after transpile ]
    Next we will get layout (without ancilla) and physical QuantumCircuit (without layout) for the Kernel.
    """

    for idx, layer in enumerate(new_block_list):
        if layer[1] == True:
            """get layout (without ancilla) for the Kernel"""
            layout1 = None
            layout2 = None
            if idx == 0 and len(new_block_list) > 1 and new_block_list[1][1] == False:
                layout2 = new_block_list[1][2]
            elif (
                idx == len(new_block_list) - 1
                and len(new_block_list) > 1
                and new_block_list[-2][1] == False
            ):
                layout1 = new_block_list[len(new_block_list) - 2][2]
            else:
                if new_block_list[idx - 1][1] == False:
                    layout1 = new_block_list[idx - 1][2]
                if new_block_list[idx + 1][1] == False:
                    layout2 = new_block_list[idx + 1][2]
            layout_without_ancilla = get_layout(
                backend=backend,
                layout_pre=layout1,
                layout_next=layout2,
                used_phy_qubits=layer[4],
                used_virtual_qubits=layer[5],
                qubit_num=qubit_num,
            )
            layer[2] = copy.deepcopy(layout_without_ancilla)
            """get physical QuantumCircuit (without layout) but Kernels folded"""
            full_ancilla_pass = FullAncillaAllocation(coupling_map=coupling_map)
            full_ancilla_pass.property_set["layout"] = copy.deepcopy(layout_without_ancilla)
            qc_temp = copy.deepcopy(layer[0])
            qc_temp._layout = None
            phy_dag = full_ancilla_pass.run(circuit_to_dag(qc_temp))
            layout_with_ancilla = full_ancilla_pass.property_set["layout"]
            apply_layout_pass = ApplyLayout()
            apply_layout_pass.property_set["layout"] = layout_with_ancilla
            phy_dag = apply_layout_pass.run(phy_dag)

            unPaulihedraled_phy_circ = dag_to_circuit(phy_dag)
            unPaulihedraled_phy_circ._layout = layout_with_ancilla
            unPaulihedraled_phy_circ._layout = None

            """get the unfolded physical Quantum Circuit"""
            Paulihedraled_phy_circ = Paulihedral_on_different_qubits_num(
                input_circuit=unPaulihedraled_phy_circ,
                global_backend=backend,
                global_coupling_map=coupling_map,
                ordering_method=ordering_method,
                backend_method=backend_method,
                do_max_iteration=do_max_iteration,
                used_phy_qubits_list=layer[4],
            )
            layer[3] = Paulihedraled_phy_circ
    """
    Now in new_block_list:
    if Kernel:
        layer= [ initial QuantumCircuit, True, layout_without_ancilla, physical QuantumCircuit, used_phy_qubits_list, used_virtual_qubits_list]
    if not Kernel:
        layer = [ initial QuantumCircuit, False, layout_without_ancilla, QuantumCircuit after transpile ]
    Next we will get the final result which is the output circuit for the function Paulihedral_on_mixed_circuit().
    """

    noise_res = NoiseAdaptiveLayout(backend.properties())
    noise_res._initialize_backend_prop()
    swap_reliabs = noise_res.swap_reliabs
    total_qubit_num = len(swap_reliabs)

    final_result = QuantumCircuit(total_qubit_num)

    """get the layout of final_result"""
    if len(new_block_list[0][2]._p2v) > total_qubit_num:
        raise TranspilerError(
            f"the virtual qubits number is more than physical qubits number, "
            f"so it's impossible to run the function Paulihedral_on_mixed_circuit()"
        )
        return None
    elif len(new_block_list[0][2]._p2v) == total_qubit_num:
        final_layout = copy.deepcopy(new_block_list[0][2])
    elif len(new_block_list[0][2]._p2v) < total_qubit_num:
        final_layout = copy.deepcopy(new_block_list[0][2])
        full_ancilla_pass = FullAncillaAllocation(coupling_map=coupling_map)
        full_ancilla_pass.property_set["layout"] = final_layout
        full_ancilla_pass.run(circuit_to_dag(copy.deepcopy(layer[0])))
        final_layout = full_ancilla_pass.property_set["layout"]

    """combine together and get the physical circuit of the final result"""
    for block in new_block_list:
        phy_qc = copy.deepcopy(block[3])
        phy_qc._layout = None
        block[3] = phy_qc
    final_result.extend(new_block_list[0][3])

    for idx in range(1, len(new_block_list)):
        layout_transform = LayoutTransformation(
            coupling_map=coupling_map,
            from_layout=new_block_list[idx - 1][2],
            to_layout=new_block_list[idx][2],
        )
        final_dag = layout_transform.run(circuit_to_dag(final_result))
        final_result = dag_to_circuit(final_dag).extend(new_block_list[idx][3])
    """let the layout of the last layer match the layout of the first one"""
    layout_transform = LayoutTransformation(
        coupling_map=coupling_map,
        from_layout=new_block_list[len(new_block_list) - 1][2],
        to_layout=new_block_list[0][2],
    )
    final_dag = layout_transform.run(circuit_to_dag(final_result))
    final_result = dag_to_circuit(final_dag)

    final_result._layout = final_layout
    return final_result
