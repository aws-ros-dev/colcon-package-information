# Copyright 2016-2018 Dirk Thomas
# Licensed under the Apache License, Version 2.0

from collections import defaultdict
from collections import OrderedDict
import itertools
import os
from pathlib import Path

from colcon_core.package_selection import add_arguments \
    as add_packages_arguments
from colcon_core.package_selection import get_package_descriptors
from colcon_core.package_selection import select_package_decorators
from colcon_core.plugin_system import satisfies_version
from colcon_core.topological_order import topological_order_packages
from colcon_core.verb import VerbExtensionPoint


class ListVerb(VerbExtensionPoint):
    """List packages, optionally in topological ordering."""

    def __init__(self):  # noqa: D107
        super().__init__()
        satisfies_version(VerbExtensionPoint.EXTENSION_POINT_VERSION, '^1.0')

    def add_arguments(self, *, parser):  # noqa: D102
        # only added so that package selection arguments can be used
        # which use the build directory to store state information
        parser.add_argument(
            '--build-base',
            default='build',
            help='The base path for all build directories (default: build)')

        add_packages_arguments(parser)

        parser.add_argument(
            '--topological-order', '-t',
            action='store_true',
            default=False,
            help='Order output based on topological ordering (breadth-first)')

        group = parser.add_mutually_exclusive_group()
        group.add_argument(
            '--names-only', '-n',
            action='store_true',
            default=False,
            help='Output only the name of each package but not the path')
        group.add_argument(
            '--paths-only', '-p',
            action='store_true',
            default=False,
            help='Output only the path of each package but not the name')
        group.add_argument(
            '--topological-graph', '-g',
            action='store_true',
            default=False,
            help='Output topological graph in ASCII '
                 '(implies --topological-order)')
        group.add_argument(
            '--topological-graph-dot',
            action='store_true',
            default=False,
            help='Output topological graph in DOT '
                 '(e.g. pass the output to dot: ` | dot -Tpng -o graph.png`), '
                 'legend: blue=build, red=run, tan=test, dashed=indirect')

        parser.add_argument(
            '--topological-graph-density',
            action='store_true',
            default=False,
            help='Output density for topological graph (only affects '
                 '--topological-graph)')

        parser.add_argument(
            '--topological-graph-legend',
            action='store_true',
            default=False,
            help='Output legend for topological graph (only affects '
                 '--topological-graph)')
        parser.add_argument(
            '--topological-graph-dot-cluster',
            action='store_true',
            default=False,
            help='Cluster packages by their filesystem path '
                 '(only affects --topological-graph-dot)')

    def main(self, *, context):  # noqa: D102
        args = context.args
        if args.topological_graph or args.topological_graph_dot:
            args.topological_order = True

        descriptors = get_package_descriptors(args)

        # always perform topological order for the select package extensions
        decorators = topological_order_packages(
            descriptors, recursive_categories=('run', ))

        select_package_decorators(args, decorators)

        if args.topological_graph:
            if args.topological_graph_legend:
                print('+ marks when the package in this row can be processed')
                print('* marks a direct dependency '
                      'from the package indicated by the + in the same column '
                      'to the package in this row')
                print('. marks a transitive dependency')
                print()

            # draw dependency graph in ASCII
            shown_decorators = list(filter(lambda d: d.selected, decorators))
            max_length = max([
                len(m.descriptor.name) for m in shown_decorators] + [0])
            lines = [
                m.descriptor.name.ljust(max_length + 2)
                for m in shown_decorators]
            depends = [
                m.descriptor.get_dependencies() for m in shown_decorators]
            rec_depends = [
                m.descriptor.get_recursive_dependencies(
                    [d.descriptor for d in decorators],
                    recursive_categories=('run', ))
                for m in shown_decorators]

            empty_cells = 0
            for i, decorator in enumerate(shown_decorators):
                for j in range(len(lines)):
                    if j == i:
                        # package i is being processed
                        lines[j] += '+'
                    elif shown_decorators[j].descriptor.name in depends[i]:
                        # package i directly depends on package j
                        lines[j] += '*'
                    elif shown_decorators[j].descriptor.name in rec_depends[i]:
                        # package i recursively depends on package j
                        lines[j] += '.'
                    else:
                        # package i doesn't depend on package j
                        lines[j] += ' '
                        empty_cells += 1
            if args.topological_graph_density:
                empty_fraction = \
                    empty_cells / (len(lines) * (len(lines) - 1)) \
                    if len(lines) > 1 else 1.0
                # normalize to 200% since half of the matrix should be empty
                density_percentage = 200.0 * (1.0 - empty_fraction)
                print('dependency density %.2f %%' % density_percentage)
                print()

        elif args.topological_graph_dot:
            lines = ['digraph graphname {']

            decorators_by_name = defaultdict(set)
            for deco in decorators:
                decorators_by_name[deco.descriptor.name].add(deco)

            selected_pkg_names = [
                m.descriptor.name for m in decorators if m.selected]
            has_duplicate_names = \
                len(selected_pkg_names) != len(set(selected_pkg_names))
            selected_pkg_names = set(selected_pkg_names)

            # collect selected package descriptors and their parent path
            nodes = OrderedDict()
            for deco in reversed(decorators):
                if not deco.selected:
                    continue
                nodes[deco.descriptor] = Path(deco.descriptor.path).parent

            # collect direct dependencies
            direct_edges = defaultdict(set)
            for deco in reversed(decorators):
                if not deco.selected:
                    continue
                # iterate over dependency categories
                for category, deps in deco.descriptor.dependencies.items():
                    # iterate over dependencies
                    for dep in deps:
                        if dep not in selected_pkg_names:
                            continue
                        # store the category of each dependency
                        # use the decorator descriptor
                        # since there might be packages with the same name
                        direct_edges[(deco.descriptor, dep)].add(category)

            # collect indirect dependencies
            indirect_edges = defaultdict(set)
            for deco in reversed(decorators):
                if not deco.selected:
                    continue
                # iterate over dependency categories
                for category, deps in deco.descriptor.dependencies.items():
                    # iterate over dependencies
                    for dep in deps:
                        # ignore direct dependencies
                        if dep in selected_pkg_names:
                            continue
                        # ignore unknown dependencies
                        if dep not in decorators_by_name.keys():
                            continue
                        # iterate over recursive dependencies
                        for rdep in itertools.chain.from_iterable(
                            d.recursive_dependencies
                            for d in decorators_by_name[dep]
                        ):
                            if rdep not in selected_pkg_names:
                                continue
                            # skip edges which are redundant to direct edges
                            if (deco.descriptor, rdep) in direct_edges:
                                continue
                            indirect_edges[(deco.descriptor, rdep)].add(
                                category)

            try:
                # HACK Python 3.5 can't handle Path objects
                common_path = os.path.commonpath(
                    [str(p) for p in nodes.values()])
            except ValueError:
                common_path = None

            def get_node_data(descriptor):
                nonlocal has_duplicate_names
                if not has_duplicate_names:
                    # use name where possible so the dot code is easy to read
                    return descriptor.name, ''
                # otherwise append the descriptor id to make each node unique
                descriptor_id = id(descriptor)
                return (
                    '{descriptor.name}_{descriptor_id}'.format_map(locals()),
                    ' [label = "{descriptor.name}"]'.format_map(locals()),
                )

            if not args.topological_graph_dot_cluster or common_path is None:
                # output nodes
                for desc in nodes.keys():
                    node_name, attributes = get_node_data(desc)
                    lines.append(
                        '  "{node_name}"{attributes};'.format_map(locals()))
            else:
                # output clusters
                clusters = defaultdict(set)
                for desc, path in nodes.items():
                    clusters[path.relative_to(common_path)].add(desc)
                for i, cluster in zip(range(len(clusters)), clusters.items()):
                    path, descs = cluster
                    if path.name:
                        # wrap cluster in subgraph
                        lines.append(
                            '  subgraph cluster_{i} {{'.format_map(locals()))
                        lines.append(
                            '    label = "{path}";'.format_map(locals()))
                        indent = '    '
                    else:
                        indent = '  '
                    for desc in descs:
                        node_name, attributes = get_node_data(desc)
                        lines.append(
                            '{indent}"{node_name}"{attributes};'
                            .format_map(locals()))
                    if path.name:
                        lines.append('  }')

            # output edges
            color_mapping = OrderedDict((
                ('build', 'blue'),
                ('run', 'red'),
                ('test', 'tan'),
            ))
            for style, edges in zip(
                ('', ', style="dashed"'),
                (direct_edges, indirect_edges),
            ):
                for (desc_start, node_end), categories in edges.items():
                    colors = ':'.join([
                        color for category, color in color_mapping.items()
                        if category in categories])
                    start_name, _ = get_node_data(desc_start)
                    for deco in decorators_by_name[node_end]:
                        end_name, _ = get_node_data(deco.descriptor)
                        lines.append(
                            '  "{start_name}" -> "{end_name}" '
                            '[color="{colors}"{style}];'.format_map(locals()))

            lines.append('}')

        else:
            if not args.topological_order:
                decorators = sorted(
                    decorators, key=lambda d: d.descriptor.name)
            lines = []
            for decorator in decorators:
                if not decorator.selected:
                    continue
                pkg = decorator.descriptor
                if args.names_only:
                    lines.append(pkg.name)
                elif args.paths_only:
                    lines.append(str(pkg.path))
                else:
                    lines.append(
                        pkg.name + '\t' + str(pkg.path) + '\t(%s)' % pkg.type)
            if not args.topological_order:
                # output names and / or paths in alphabetical order
                lines.sort()

        for line in lines:
            print(line)
