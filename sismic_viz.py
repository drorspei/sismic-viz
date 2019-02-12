from __future__ import print_function

import os
import time
import shutil
import pprint
import argparse
import webbrowser
from flask import Flask, send_file, request
from sismic.io import import_from_yaml, export_to_plantuml
from sismic.model import Event, CompositeStateMixin, CompoundState
from sismic.interpreter import Interpreter
import tempfile


yaml_filepath = None
imagefile_path = ""
interp = None  # type: Interpreter
global_config = {
    "file_type": "dot",
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

template_leaf = "\n{state_name} [label={label} shape=Mrecord{style} color={color}]"

template_leaf_table_label = """\n{state_name} [label=<
  <table cellborder="0" style="rounded"{bgcolor}>
    <tr><td>{state_name}</td></tr>
    <hr/>{on_entry}{on_exit}
  </table>
> shape=none margin=0]"""

template_transition = "\n{source} -> {target} [label=\"{label}\"{ltail}{lhead}{dir}{color}]"


def visit_state(sc, state_name, configuration=()):
    state = sc.state_for(state_name)
    active = state_name in configuration

    if isinstance(state, CompositeStateMixin):
        color = "\"#3399ff\"" if active else "black"

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

    if state.on_entry or state.on_exit:
        bgcolor = " bgcolor=\"#3399ff\"" if active else ""
        on_entry = "\n    <tr><td>entry / {}</td></tr>".format(state.on_entry) if state.on_entry else ""
        on_exit = "\n    <tr><td>exit / {}</td></tr>".format(state.on_exit) if state.on_exit else ""

        return template_leaf_table_label.format(state_name=state_name, bgcolor=bgcolor,
                                                on_entry=on_entry, on_exit=on_exit)
    else:
        if active:
            color = "\"#3399ff\""
            style = " style=filled"
        else:
            color = "black"
            style = ""

        label = "\"{}\"".format(state_name)

        return template_leaf.format(state_name=state_name, label=label, style=style, color=color)


def get_valid_nodes(sc, state_name):
    state = sc.state_for(state_name)

    if isinstance(state, CompositeStateMixin):
        return "invisible_{}".format(state_name), "cluster_{}".format(
            state_name
        )

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
                                           ltail=source, lhead=out_point, label="", dir_=" dir=none", color=color))
                edges.append(get_edge_text(source=out_point, target=valid_target,
                                           ltail=out_point, lhead=target, label=label, dir_="", color=color))
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
            <img src="statechart.svg?{timestamp}" style="max-width:100%; height:auto;"/>
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
                Disable KeyErrors in Actions and Guards
                <input type="submit" name="fromform" value="update"/>
            </form>
        </div>
        <div>
            Click to trigger an event:<br/>
            <form method="get">
{events}
            </form>
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

template_event = "            <button type=\"submit\" name=\"event\" value=\"{event}\">{event_repr}</button>"
template_guard = "            <input type=\"checkbox\" name=\"guard\" value=\"{guard}\">{guard_repr}</button>"


def get_font_size_options_html():
    return "\n".join(
        template_option.format(
            selected=" selected" if global_config["edge_fontsize"] == size else "",
            size=size
        )
        for size in range(6, 16, 2)
    )


def get_flask_app():
    app = Flask(__name__)

    @app.route('/', methods=['GET'])
    def display_interactive_statechart():
        global global_config

        if request.args.get("reset", False, bool):
            create_interp()

        if request.args.get("fromform", False):
            global_config["edge_fontsize"] = request.args.get("edge_fontsize", 14, int)
            global_config["include_guards"] = request.args.get("include_guards", False, bool)
            global_config["include_actions"] = request.args.get("include_actions", False, bool)
            global_config["disable_keyerror"] = request.args.get("disable_keyerror", False, bool)

        if global_config["disable_keyerror"]:
            disable_keyerror_in_actions()
        else:
            enable_keyerror_in_actions()

        event = request.args.get('event', '', str)
        if event:
            global_config["history"].append("<b>Triggered Event: <u>\"{}\"</u></b>".format(event))
            for macro_step in interp.queue(Event(event)).execute():
                global_config["history"].extend(macro_step.steps)

        create_image(interp.statechart, interp.configuration, global_config, imagefile_path)

        return template_html_doc.format(
            timestamp=time.time(),
            include_guards_checked=" checked" if global_config["include_guards"] else "",
            include_actions_checked=" checked" if global_config["include_actions"] else "",
            disable_keyerror_checked=" checked" if global_config["disable_keyerror"] else "",
            font_options=get_font_size_options_html(),
            events="<br/>\n".join(sorted(set(
                template_event.format(event=transition.event, event_repr=transition.event)
                for state in interp.configuration
                for transition in interp.statechart.transitions_from(state)
                if transition.event
            ))),
            last_output="<br/>\n".join(pprint.pformat(global_config["history"][::-1]).splitlines())
        )

    @app.route('/statechart.svg')
    def get_statechart_graph():
        return send_file(imagefile_path, mimetype="image/svg+xml")

    return app


def create_image(statechart, in_states, configuration, imagepath):
    if configuration["file_type"] == "dot":
        with tempfile.NamedTemporaryFile() as f:
            if configuration["file_type"] == "dot":
                output = export_to_dot(statechart,
                                       edge_fontsize=configuration["edge_fontsize"],
                                       include_guards=configuration["include_guards"],
                                       include_actions=configuration["include_actions"],
                                       configuration=in_states)
                f.write(output)
                f.flush()
                open("/tmp/hello.dot", "wb").write(output)
                os.system("dot -Tsvg {inpath} -o {outpath}".format(inpath=f.name, outpath=imagepath))
    else:
        dirname = tempfile.mkdtemp()
        try:
            output = export_to_plantuml(statechart)
            fname = os.path.join(dirname, "graph.puml")
            with open(fname, "wb") as f:
                f.write(output)
                f.flush()
            os.system("plantuml {inpath} -o {outpath} -tsvg".format(inpath=fname, outpath=dirname))
            open(imagefile_path, "wb").write(open(os.path.join(dirname, "graph.svg"), "rb").read())
        finally:
            shutil.rmtree(dirname)


template_bound_doc = """
<html>
    <head><meta http-equiv="refresh" content="1; URL=/"></head>
    <body>
        clock: {clock_time:10.3f}<br/>{states}<br/><a href=\"/shutdown\">shutdown server</a>
        <br/>
        <img src=\"statechart.svg?{timestamp}\" style=\"max-width:100%; height:auto;\"/>
    </body>
</html>
"""


def server_to_bind(statechart):
    configuration = []
    clock_time = [0]

    def callback(metaevent):
        """
        :type metaevent: Event
        """
        if metaevent.name == "state entered":
            configuration.append(metaevent.state)
        elif metaevent.name == "state exited":
            configuration.remove(metaevent.state)
        elif metaevent.name == "step started":
            clock_time[0] = metaevent.time

    def background_server():
        global imagefile_path

        with tempfile.NamedTemporaryFile() as imagefile:
            imagefile_path = imagefile.name
            app = Flask(__name__)

            @app.route("/")
            def index():
                create_image(statechart, configuration, global_config, imagefile_path)
                return template_bound_doc.format(clock_time=clock_time[0],
                                                 states=", ".join(configuration),
                                                 timestamp=time.time())

            def shutdown_server():
                func = request.environ.get('werkzeug.server.shutdown')
                if func is None:
                    raise RuntimeError('Not running with the Werkzeug Server')
                func()

            @app.route('/statechart.svg')
            def get_statechart_graph():
                return send_file(imagefile_path, mimetype="image/svg+xml")

            @app.route('/shutdown')
            def shutdown():
                shutdown_server()
                return 'Server shutting down...'

            webbrowser.open_new("http://127.0.0.1:5000")
            app.run(host='0.0.0.0', threaded=False)

    import threading
    threading.Thread(target=background_server).start()

    return callback


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

    if not hasattr(interp._evaluator, "old_eval_code"):
        interp._evaluator.old_eval_code = interp._evaluator._evaluate_code

        def new_eval_code(self, code, **kwargs):
            try:
                return self.old_eval_code(code, **kwargs)
            except CodeEvaluationError:
                return False

        interp._evaluator._evaluate_code = MethodType(new_eval_code, interp._evaluator)


def enable_keyerror_in_actions():
    if hasattr(interp._evaluator, "old_execute_code"):
        interp._evaluator._execute_code = interp._evaluator.old_execute_code
        del interp._evaluator.old_execute_code

    if hasattr(interp._evaluator, "old_eval_code"):
        interp._evaluator._evaluate_code = interp._evaluator.old_val_code
        del interp._evaluator.old_eal_code


def run_interactive(filepath):
    global imagefile_path, yaml_filepath

    yaml_filepath = filepath
    create_interp()
    
    with tempfile.NamedTemporaryFile() as imagefile:
        imagefile_path = imagefile.name
        webbrowser.open_new("http://127.0.0.1:5000")
        get_flask_app().run(host='0.0.0.0', threaded=False)


def main():
    global global_config

    parser = argparse.ArgumentParser()
    parser.add_argument("input_file", type=str, help="Path to input yaml file.")

    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('-it', '--interactive', action="store_true", dest="interactive",
                       help="Runs input file in a browser.")
    group.add_argument('-o', type=str, dest="output_file", help="Path to output dot file.")

    parser.add_argument('-T', type=str, default="dot", dest="file_type",
                        help="File type for output, if not in interactive mode. "
                             "If dot, produces dot file, others calls dot with \"-T{type}\". "
                             "If in interactive mode, the options are \"dot\" or \"puml\".")

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
        global_config["include_guards"] = args.include_guards
        global_config["include_actions"] = args.include_actions
        global_config["edge_fontsize"] = args.trans_font_size
        global_config["file_type"] = args.file_type

        run_interactive(args.input_file)
    else:
        sc = import_from_yaml(filepath=args.input_file)

        if args.file_type == "puml":
            export_to_plantuml(sc, filepath=args.output_file)
        else:
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
