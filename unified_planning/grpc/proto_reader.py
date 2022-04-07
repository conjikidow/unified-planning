# Copyright 2021 AIPlan4EU project
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
import fractions
from typing import OrderedDict

import unified_planning.grpc.generated.unified_planning_pb2 as unified_planning_pb2
from unified_planning.grpc.converter import Converter, handles
import unified_planning.model
from unified_planning.model.effect import ASSIGN, INCREASE, DECREASE
from unified_planning.model.operators import (
    BOOL_CONSTANT,
    FLUENT_EXP,
    INT_CONSTANT,
    OBJECT_EXP,
    PARAM_EXP,
    REAL_CONSTANT,
)
import unified_planning.plan
from unified_planning.model import (
    Effect,
    ActionParameter,
    Problem,
    DurativeAction,
    InstantaneousAction,
)
from unified_planning.shortcuts import BoolType, UserType, RealType, IntType


def convert_type_str(s, env):
    if s == "bool":
        value_type = env.type_manager.BoolType()
    elif s == "int":
        value_type = env.type_manager.IntType()  # TODO: deal with bounds
    elif s == "float":
        value_type = env.type_manager.RealType()  # TODO: deal with bounds
    elif "real" in s:
        a = float(s.split("[")[1].split(",")[0])
        b = float(s.split(",")[1].split("]")[0])
        value_type = env.type_manager.RealType(a, b)  # TODO: deal with bounds
    else:
        value_type = env.type_manager.UserType(s)
    return value_type


class ProtobufReader(Converter):
    current_action = None

    @handles(unified_planning_pb2.Parameter)
    def _convert_parameter(self, msg, problem):
        # TODO: Convert parameter names into parameter types?
        return ActionParameter(
            msg.name,
            convert_type_str(msg.type, problem.env),
        )

    @handles(unified_planning_pb2.Fluent)
    def _convert_fluent(self, msg, problem):
        value_type = convert_type_str(msg.value_type, problem.env)
        sig = []
        for p in msg.parameters:
            sig.append(
                convert_type_str(p.type, problem.env)
            )  # TODO: Ignores p.name from parameter message
        fluent = unified_planning.model.Fluent(msg.name, value_type, sig, problem.env)
        return fluent

    @handles(unified_planning_pb2.ObjectDeclaration)
    def _convert_object(self, msg, problem):
        obj = unified_planning.model.Object(
            msg.name, problem.env.type_manager.UserType(msg.type)
        )
        return obj

    @handles(unified_planning_pb2.Expression)
    def _convert_expression(self, msg, problem, param_map):
        payload = self.convert(msg.atom, problem)
        args = []
        for arg_msg in msg.list:
            args.append(self.convert(arg_msg, problem, param_map))

        if msg.kind == unified_planning_pb2.ExpressionKind.Value("CONSTANT"):
            assert msg.atom is not None

            if msg.type == "bool":
                return problem.env.expression_manager.create_node(
                    node_type=BOOL_CONSTANT,
                    args=tuple(args),
                    payload=payload,
                )
            elif msg.type == "int":
                return problem.env.expression_manager.create_node(
                    node_type=INT_CONSTANT,
                    args=tuple(args),
                    payload=payload,
                )
            elif msg.type == "real":
                return problem.env.expression_manager.create_node(
                    node_type=REAL_CONSTANT,
                    args=tuple(args),
                    payload=payload,
                )
        elif msg.kind == unified_planning_pb2.ExpressionKind.Value("PARAMETER"):
            # IN UP, parameters are the user types and not the parameter name itself
            return problem.env.expression_manager.create_node(
                node_type=PARAM_EXP,
                args=tuple(args),
                payload=ActionParameter(
                    msg.atom.symbol, problem.env.type_manager.UserType(msg.type)
                ),
            )
        elif msg.kind == unified_planning_pb2.ExpressionKind.Value("FLUENT_SYMBOL"):
            assert problem.has_fluent(msg.atom.symbol)
            return problem.env.expression_manager.create_node(
                node_type=FLUENT_EXP,
                args=tuple(args),
                payload=payload,
            )
        elif msg.kind == unified_planning_pb2.ExpressionKind.Value("FUNCTION_SYMBOL"):
            # TODO: complete the function symbol conversion
            return
        elif msg.kind == unified_planning_pb2.ExpressionKind.Value("STATE_VARIABLE"):
            atom = self.convert(msg.atom, problem)
            return problem.env.expression_manager.create_node(
                node_type=OBJECT_EXP, args=(), payload=payload
            )
        elif msg.kind == unified_planning_pb2.ExpressionKind.Value(
            "FUNCTION_APPLICATION"
        ):
            # TODO: complete the function application conversion
            return

        return

    @handles(unified_planning_pb2.Atom)
    def _convert_atom(self, msg, problem):
        field = msg.WhichOneof("content")
        # No atom
        if field is None:
            return None

        value = getattr(msg, field)
        if field == "int":
            return problem.env.expression_manager.Int(value)
        elif field == "real":
            return problem.env.expression_manager.Real(value)
        elif field == "boolean":
            # TODO: fix BoolExp returning always False Expressions
            return problem.env.expression_manager.Bool(value)
        else:
            if problem.has_object(value):
                return problem.object(value)
            elif self.current_action is not None:
                try:
                    return problem.env.expression_manager.ParameterExp(
                        self.current_action.parameter(value)
                    )
                except KeyError:
                    return problem.fluent(value)
        return problem.fluent(value)

    @handles(unified_planning_pb2.TypeDeclaration)
    def _convert_type_declaration(self, msg):
        if msg.type_name == "bool":
            return BoolType()
        elif msg.type_name.startswith("integer["):
            tmp = msg.type_name.split("[")[1].split("]")[0].split(", ")
            lb = None
            ub = None
            if tmp[0] != "-inf":
                lb = int(tmp[0])
            elif tmp[1] != "inf":
                ub = int(tmp[1])
            return IntType(lower_bound=lb, upper_bound=ub)
        elif msg.type_name.startswith("real["):
            tmp = msg.type_name.split("[")[1].split("]")[0].split(", ")
            lb = None
            ub = None
            if tmp[0] != "-inf":
                lb = fractions.Fraction(tmp[0])
            elif tmp[1] != "inf":
                ub = fractions.Fraction(tmp[1])
            return RealType(lower_bound=lb, upper_bound=ub)
        else:
            parent = None
            if parent != "":
                parent = UserType(msg.parent_type)
            return UserType(msg.type_name, parent)

    @handles(unified_planning_pb2.Problem)
    def _convert_problem(self, msg, problem):
        PROBLEM = Problem(name=msg.problem_name, env=problem.env)
        for obj in msg.objects:
            PROBLEM.add_object(self.convert(obj, problem))
        for f in msg.fluents:
            PROBLEM.add_fluent(self.convert(f, problem))
        for f in msg.actions:
            PROBLEM.add_action(self.convert(f, problem))
        for eff in msg.timed_effects:
            PROBLEM.add_timed_effect(self.convert(eff, problem))

        for assign in msg.initial_state:
            (fluent, value) = self.convert(assign, problem)
            PROBLEM.set_initial_value(fluent, value)

        for goal in msg.goals:
            PROBLEM.add_goal(self.convert(goal, problem))

        # TODO: add features

        return PROBLEM

    @handles(unified_planning_pb2.Assignment)
    def _convert_initial_state(self, msg, problem):
        return (self.convert(msg.fluent, problem), self.convert(msg.value, problem))

    @handles(unified_planning_pb2.Goal)
    def _convert_goal(self, msg, problem):
        goal = self.convert(msg.goal, problem)
        if msg.timing is not None:
            timing = self.convert(msg.timing)
            # TODO: deal with timed goals
            return goal
        else:
            return goal

    @handles(unified_planning_pb2.Action)
    def _convert_action(self, msg, problem):
        # TODO: fix `NOT`conditions and assignments are currently returning None
        # TODO: fix `FUNCTION_SYMBOLS` are currently returning None

        parameters = OrderedDict()
        action: unified_planning.model.Action

        for param in msg.parameters:
            parameters[param.name] = self.convert(param, problem)

        if msg.HasField("duration"):
            action = DurativeAction(msg.name, parameters)
            action.set_duration_constraint(self.convert(msg.duration, problem))
        else:
            action = InstantaneousAction(msg.name, parameters)

        self.current_action = action
        for cond in msg.conditions:
            exp = self.convert(cond.cond, problem, parameters)
            if exp is None:
                continue
            try:
                action.add_condition(self.convert(cond.span), exp)
            except AttributeError:
                action.add_precondition(exp)

        for eff in msg.effects:
            exp = self.convert(eff.effect, problem, parameters)
            if exp.fluent() is None or exp.value() is None:
                continue
            try:
                action.add_timed_effect(
                    timing=self.convert(eff.occurence_time),
                    fluent=exp.fluent(),
                    value=exp.value(),
                )
            except AttributeError:
                action.add_effect(fluent=exp.fluent(), value=exp.value())

        if msg.HasField("cost"):
            action.set_cost(self.convert(msg.cost, problem))

        return action

    @handles(unified_planning_pb2.EffectExpression)
    def _convert_effect(self, msg, problem, param_map):
        # EffectKind
        kind = 0
        if msg.kind == unified_planning_pb2.EffectExpression.EffectKind.Value("ASSIGN"):
            kind = ASSIGN
        elif msg.kind == unified_planning_pb2.EffectExpression.EffectKind.Value(
            "INCREASE"
        ):
            kind = INCREASE
        elif msg.kind == unified_planning_pb2.EffectExpression.EffectKind.Value(
            "DECREASE"
        ):
            kind = DECREASE

        if msg.HasField("condition"):
            return Effect(
                self.convert(msg.fluent, problem, param_map),
                self.convert(msg.value, problem, param_map),
                self.convert(msg.condition, problem, param_map),
                kind,
            )
        else:
            return Effect(
                self.convert(msg.fluent, problem, param_map),
                self.convert(msg.value, problem, param_map),
                kind,
            )

    @handles(unified_planning_pb2.TimeInterval)
    def _convert_timed_interval(self, msg):
        return unified_planning.model.TimeInterval(
            lower=self.convert(msg.lower),
            upper=self.convert(msg.upper),
            is_left_open=msg.is_left_open,
            is_right_open=msg.is_right_open,
        )

    @handles(unified_planning_pb2.Timing)
    def _convert_timing(self, msg):
        return unified_planning.model.Timing(
            delay=int(msg.delay), timepoint=self.convert(msg.timepoint)
        )

    @handles(unified_planning_pb2.Timepoint)
    def _convert_timepoint(self, msg):
        if msg.kind == unified_planning_pb2.Timepoint.TimepointKind.Value(
            "GLOBAL_START"
        ):
            return unified_planning.model.timing.Timepoint(
                kind=unified_planning.model.timing.GLOBAL_START
            )
        elif msg.kind == unified_planning_pb2.Timepoint.TimepointKind.Value(
            "GLOBAL_END"
        ):
            return unified_planning.model.timing.Timepoint(
                kind=unified_planning.model.timing.GLOBAL_END
            )
        elif msg.kind == unified_planning_pb2.Timepoint.TimepointKind.Value("START"):
            return unified_planning.model.timing.Timepoint(
                kind=unified_planning.model.timing.START
            )
        elif msg.kind == unified_planning_pb2.Timepoint.TimepointKind.Value("END"):
            return unified_planning.model.timing.Timepoint(
                kind=unified_planning.model.timing.END
            )

    @handles(unified_planning_pb2.Plan)
    def _convert_plan(self, msg, problem):
        return unified_planning.plan.SequentialPlan(
            actions=[self.convert(a, problem) for a in msg.actions]
        )

    @handles(unified_planning_pb2.ActionInstance)
    def _convert_action_instance(self, msg, problem):
        # action instance paramaters are atoms but in UP they are FNodes
        # converting to up.model.FNode
        parameters = []
        for param in msg.parameters:
            assert param.HasField("symbol")
            assert problem.has_object(param.symbol)

            parameters.append(
                problem.env.expression_manager.create_node(
                    node_type=OBJECT_EXP, args=(), payload=problem.object(param.symbol)
                )
            )

        return unified_planning.plan.ActionInstance(
            problem.action(msg.action_name),
            parameters,
        )
