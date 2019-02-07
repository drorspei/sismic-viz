from __future__ import print_function

import os
import time
import pprint
import argparse
import webbrowser
from flask import Flask, send_file, request
from sismic.io import import_from_yaml
from sismic.model import Event, CompositeStateMixin, CompoundState
from sismic.interpreter import Interpreter
import tempfile


yaml_filepath = None
imagefile_path = ""
interp = None  # type: Interpreter
config = {
    "edge_fontsize": 14,
    "include_guards": True,
    "include_actions": True,
    "disable_keyerror": True,
    "history": []
}


def indent(s):
    return '\n'.join('  ' + line for line in s.splitlines())


template_graph_doc = """digraph {{
  compound=true;
  edge [ fontsize={fontsize} ];
  label = <<b>{name}</b>>{nodes}{edges}
}}"""

template_cluster = """
subgraph cluster_{state_name} {{
  label = "{state_name}"
  color = {color}
 {style}
  node [shape=Mrecord width=.4 height=.4];{inner_nodes}{initial}{additional_points}
}}"""

template_initial = """
  node [shape=point width=.25 height=.25];
  initial_{state_name} -> {initial_state}"""

template_invisible = """
  node [shape=point style=invisible width=0 height=0];
  invisible_{state_name}"""

template_leaf = "\n{state_name} [label=\"{state_name}\" shape=Mrecord{style} color={color}]"

template_transition = "\n{source} -> {target} [label=\"{label}\"{ltail}{lhead}{dir}{color}]"


def visit_state(sc, state_name, configuration=()):
    state = sc.state_for(state_name)
    color = "black"
    style = ""

    if state_name in configuration:
        color = "\"#3399ff\""
        style = " style=filled"

    if isinstance(state, CompositeStateMixin):
        if isinstance(state, CompoundState):
            style = " style=rounded"
            initial = template_initial.format(state_name=state_name, initial_state=state.initial)
        else:
            style = " style=dashed"
            initial = ""

        # If there are transitions to/from this composite state, we add an invisible point.
        if sc.transitions_to(state_name) or sc.transitions_from(state_name):
            initial = "{}{}".format(initial, template_invisible.format(state_name=state_name))

        inner_nodes = '\n'.join(indent(visit_state(sc, inner, configuration=configuration))
                                for inner in sc.children_for(state_name))

        additional_points = '\n'.join(
            "  point_{child}_{ind}".format(child=child, ind=ind)
            for child in sc.children_for(state_name)
            for ind, transition in enumerate(sc.transitions_from(child))
            if transition.target in sc.descendants_for(child))
        if additional_points:
            additional_points = '\n{}\n{}'.format("  node [shape=point margin=0 style=invis width=0. height=0.]",
                                                  additional_points)

        return template_cluster.format(state_name=state_name, initial=initial, inner_nodes=inner_nodes,
                                       style=style, additional_points=additional_points, color=color)

    return template_leaf.format(state_name=state_name, style=style, color=color)


def get_valid_nodes(sc, state_name):
    state = sc.state_for(state_name)

    if isinstance(state, CompositeStateMixin):
        return "invisible_{}".format(state_name), "cluster_{}".format(state_name)

    return state_name, state_name


def get_edge_text(source, target, ltail, lhead, label, dir_, color):
    if ltail == source:
        ltail = ""
    else:
        ltail = " ltail={}".format(ltail)

    if lhead == target:
        lhead = ""
    else:
        lhead = " lhead={}".format(lhead)

    return template_transition.format(source=source, target=target,
                                      ltail=ltail, lhead=lhead, label=label, dir=dir_, color=color)


def get_edges(sc, include_guards, include_actions, configuration=()):
    edges = []
    for state_name in sc.states:
        for ind, transition in enumerate(sc.transitions_from(state_name)):
            valid_source, source = get_valid_nodes(sc, transition.source)
            valid_target, target = get_valid_nodes(sc, transition.target)

            color = ""
            label_parts = []

            if transition.event:
                label_parts.append(transition.event)
                if state_name in configuration:
                    color = " color=\"#3399ff\""
            if include_guards and transition.guard:
                label_parts.append('[{}]'.format(transition.guard.replace('"', '\\"')))
            if include_actions and transition.action:
                label_parts.append('/ {}'.format(transition.action.replace('"', '\\"')))

            label = " ".join(label_parts)

            if transition.target in sc.descendants_for(state_name):
                out_point = "point_{}_{}".format(state_name, ind)
                edges.append(get_edge_text(source=valid_source, target=out_point,
                                           ltail=source, lhead=target, label="", dir_=" dir=none", color=color))
                edges.append(get_edge_text(source=out_point, target=valid_target,
                                           ltail=source, lhead=target, label=label, dir_="", color=color))
            else:
                edges.append(get_edge_text(source=valid_source, target=valid_target,
                                           ltail=source, lhead=target, label=label, dir_="", color=color))

    return "".join(edges)


def export_to_dot(sc, include_guards=True, include_actions=True, edge_fontsize=14, configuration=()):
    nodes = indent(visit_state(sc, sc.root, configuration=configuration))
    edges = indent(get_edges(sc, include_guards, include_actions, configuration=configuration))

    return template_graph_doc.format(name=sc.name, nodes=nodes, edges=edges, fontsize=edge_fontsize)


template_option = """                    <option{selected}>{size}</option>"""


template_html_doc = """<html>
    <head>
        <title>Sismic Interactive Interpreter</title>
        <style>
            a:visited {{
              color: blue;
            }}
        </style>
    </head>
    <body>
        <div>
            <img src="statechart.png?{timestamp}" style="max-width:100%; height:auto;"/>
        </div>
        <div>
            <form method="get">
                <input type="checkbox" name="include_guards" value="True"{include_guards_checked}/> Show Guards,
                <input type="checkbox" name="include_actions" value="True"{include_actions_checked}/> Show Actions,
                Font Size: 
                <select name="edge_fontsize">
{font_options}
                </select>,
                <input type="checkbox" name="disable_keyerror" value="True"{disable_keyerror_checked}/>
                Disable KeyErrors in Actions
                <input type="submit" name="fromform" value="update"/>
            </form>
        </div>
        <div>
            Click to trigger an event:<br/>
{events}
        </div>
        <br/>
        <div>
            <a href="/?reset=True">Click here</a> to start from the beginning.
        </div>
        <br/>
        <div>
            History of events and micro steps in reverse order:<br/><br/>
{last_output}
        </div>
    </body>
</html>
"""

template_event = "            <a href=\"/?event={event}\">{event_repr}</a>"


def get_font_size_options_html():
    return "\n".join(
        template_option.format(
            selected=" selected" if config["edge_fontsize"] == size else "",
            size=size
        )
        for size in range(6, 16, 2)
    )


def get_flask_app():
    app = Flask(__name__)

    @app.route('/', methods=['GET'])
    def display_interactive_statechart():
        global config

        if request.args.get("reset", False, bool):
            create_interp()

        if request.args.get("fromform", False):
            config["edge_fontsize"] = request.args.get("edge_fontsize", 14, int)
            config["include_guards"] = request.args.get("include_guards", False, bool)
            config["include_actions"] = request.args.get("include_actions", False, bool)
            config["disable_keyerror"] = request.args.get("disable_keyerror", False, bool)

        if config["disable_keyerror"]:
            disable_keyerror_in_actions()
        else:
            enable_keyerror_in_actions()

        event = request.args.get('event', '', str)
        if event:
            config["history"].append("<b>Triggered Event: <u>\"{}\"</u></b>".format(event))
            for macro_step in interp.queue(Event(event)).execute():
                config["history"].extend(macro_step.steps)

        create_image(interp)

        return template_html_doc.format(
            timestamp=time.time(),
            include_guards_checked=" checked" if config["include_guards"] else "",
            include_actions_checked=" checked" if config["include_actions"] else "",
            disable_keyerror_checked=" checked" if config["disable_keyerror"] else "",
            font_options=get_font_size_options_html(),
            events="<br/>\n".join(template_event.format(event=transition.event, event_repr=transition.event)
                                  for state in interp.configuration
                                  for transition in interp.statechart.transitions_from(state)
                                  if transition.event),
            last_output="<br/>\n".join(pprint.pformat(config["history"][::-1]).splitlines())
        )

    @app.route('/statechart.png')
    def get_statechart_graph():
        return send_file(imagefile_path, mimetype="image/png")

    return app


def create_image(interpreter):
    global config
    with tempfile.NamedTemporaryFile() as f:
        f.write(export_to_dot(interpreter.statechart,
                              edge_fontsize=config["edge_fontsize"],
                              include_guards=config["include_guards"],
                              include_actions=config["include_actions"],
                              configuration=interpreter.configuration))
        f.flush()
        os.system("dot -Tpng {inpath} -o {outpath}".format(inpath=f.name, outpath=imagefile_path))


def create_interp():
    global interp, yaml_filepath

    daemon = import_from_yaml(filepath=yaml_filepath)
    interp = Interpreter(daemon)
    interp.execute()


class CallMe(object):
    def __call__(self, *args, **kwargs):
        return self

    def __getattribute__(self, name):
        return self


class NoKeyErrorDict(dict):
    def __init__(self, globals_, locals_):
        dict.__init__(self, globals_, **locals_)
        self.globals_ = globals_
        self.locals_ = locals_

    def __setitem__(self, name, value):
        self.locals_[name] = value

    def __getitem__(self, name):
        try:
            return self.locals_[name]
        except KeyError:
            try:
                return self.globals_[name]
            except KeyError:
                return CallMe()


def disable_keyerror_in_actions():
    from future.utils import raise_from
    from sismic.exceptions import CodeEvaluationError
    from types import MethodType

    if not hasattr(interp._evaluator, "old_execute_code"):
        interp._evaluator.old_execute_code = interp._evaluator._execute_code

        def new_execute_code(self, code, **kwargs):
            additional_context = kwargs.get("additional_context")

            if code is None:
                return []

            compiled_code = self._executable_code.get(code, None)
            if compiled_code is None:
                compiled_code = self._executable_code.setdefault(code, compile(code, '<string>', 'exec'))

            exposed_context = {
                'active': self._time_provider.active,
                'time': self._time_provider.time,
                'send': self._event_provider.send,
                'notify': self._event_provider.notify,
                'setdefault': self._setdefault,
            }
            exposed_context.update(additional_context if additional_context is not None else {})

            try:
                exec(compiled_code, NoKeyErrorDict(exposed_context, self._context))  # type: ignore
                return self._event_provider.pending
            except Exception as e:
                raise_from(CodeEvaluationError('"{}" occurred while executing "{}"'.format(e, code)), e)

        interp._evaluator._execute_code = MethodType(new_execute_code, interp._evaluator)


def enable_keyerror_in_actions():
    if hasattr(interp._evaluator, "old_execute_code"):
        interp._evaluator._execute_code = interp._evaluator.old_execute_code
        del interp._evaluator.old_execute_code


def run_interactive(filepath):
    global imagefile_path, yaml_filepath

    yaml_filepath = filepath
    create_interp()
    
    with tempfile.NamedTemporaryFile() as imagefile:
        imagefile_path = imagefile.name
        webbrowser.open_new("http://127.0.0.1:5000")
        get_flask_app().run(host='0.0.0.0', threaded=False)


def main():
    global config

    parser = argparse.ArgumentParser()
    parser.add_argument("input_file", type=str, help="Path to input yaml file.")

    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('-it', '--interactive', action="store_true", dest="interactive",
                       help="Runs input file in a browser.")
    group.add_argument('-o', type=str, dest="output_file", help="Path to output dot file.")

    parser.add_argument('-T', type=str, default="dot", dest="file_type",
                        help="File type for output, if not in interactive mode. "
                             "If dot, produces dot file, others calls dot with \"-T{type}\"")

    parser.add_argument("--no-guards", action="store_false", dest="include_guards",
                        help="Don't show transition guards")
    parser.set_defaults(include_guards=True)

    parser.add_argument("--no-actions", action="store_false", dest="include_actions",
                        help="Don't show tranision actions.")
    parser.set_defaults(include_actions=True)

    parser.add_argument("--trans-font-size", type=int, default=14,
                        help="Set font size of text on transitions. Default: 14.")
    args = parser.parse_args()

    if args.interactive:
        config["include_guards"] = args.include_guards
        config["include_actions"] = args.include_actions
        config["edge_fontsize"] = args.trans_font_size

        run_interactive(args.input_file)
    else:
        sc = import_from_yaml(filepath=args.input_file)
        dot = export_to_dot(sc=sc, include_guards=args.include_guards, include_actions=args.include_actions,
                            edge_fontsize=args.trans_font_size)
        if args.file_type == "dot":
            open(args.output_file, "w").write(dot)
        else:
            with tempfile.NamedTemporaryFile() as f:
                f.write(dot)
                f.flush()
                os.system("dot -T{file_type} {inpath} -o {outpath}".format(file_type=args.file_type, inpath=f.name,
                                                                           outpath=args.output_file))


if __name__ == '__main__':
    main()
