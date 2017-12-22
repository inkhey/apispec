# -*- coding: utf-8 -*-
"""Marshmallow plugin for apispec. Allows passing a marshmallow
`Schema` to `APISpec.definition <apispec.APISpec.definition>`
and `APISpec.add_path <apispec.APISpec.add_path>` (for responses).

Requires marshmallow>=2.0.

::

    from pprint import pprint

    from marshmallow import Schema, fields

    class UserSchema(Schema):
        id = fields.Int(dump_only=True)
        name = fields.Str(description="The user's name")

    spec.definition('User', schema=UserSchema)
    pprint(spec.to_dict()['definitions'])
    # {'User': {'properties': {'id': {'format': 'int32', 'type': 'integer'},
    #                         'name': {'description': "The user's name",
    #                                 'type': 'string'}},
    #         'type': 'object'}}

"""
from __future__ import absolute_import

import marshmallow
import copy
from apispec.core import Path
from apispec.utils import load_operations_from_docstring
from . import swagger

NAME = 'apispec.ext.marshmallow'


def get_schema_instance(schema):
    """Return schema instance for given schema (instance or class)
    :param schema: instance or class of marshmallow.Schema
    :return: schema instance of given schema (instance or class)
    """
    if isinstance(schema, type):
        return schema()
    else:
        return schema


def get_schema_ref(schema):
    """Return schema class for given schema (instance or class)
    :param schema: instance or class of marshmallow.Schema
    :return: schema class of given schema (instance or class)
    """
    if isinstance(schema, type):
        return schema
    else:
        parent_schema = type(schema)
        # Check if new schema needed
        class_name = ""
        exclude = getattr(schema, 'exclude', None)
        only = getattr(schema, 'only', None)
        if exclude:
            class_name += 'Without'
            for elem in exclude:
                class_name += '_'
                class_name += elem
        if only:
            class_name += 'Only'
            for elem in only:
                class_name += '_'
                class_name += elem
        if class_name:
            class_name = parent_schema.__name__ + class_name
            return class_name
        return type(schema)


def inspect_schema_for_auto_referencing(spec, original_schema_instance):
    """Parse given schema instance and reference eventual nested schemas
    :param spec: apispec.core.APISpec instance
    :param original_schema_instance: schema to parse
    """
    # spec.schema_name_resolver must be provided to use this function
    assert spec.schema_name_resolver

    plug = spec.plugins[NAME]
    if 'refs' not in plug:
        plug['refs'] = {}

    for field_name, field in original_schema_instance.fields.items():
        nested_schema = None
        nested_schema_ref = None

        if isinstance(field, marshmallow.fields.Nested):
            nested_schema = field.schema
            nested_schema_ref = get_schema_ref(field.schema)

        elif isinstance(field, marshmallow.fields.List) \
                and isinstance(field.container, marshmallow.fields.Nested):
            nested_schema = field.schema
            nested_schema_ref = get_schema_ref(field.container.schema)

        if nested_schema_ref:
            if nested_schema_ref not in plug['refs']:
                definition_name = spec.schema_name_resolver(
                    nested_schema_ref,
                )
                if nested_schema.many:
                    nested_schema = copy.copy(nested_schema)
                    nested_schema.many = False
                if definition_name:
                    spec.definition(
                        definition_name,
                        schema=nested_schema,
                    )


def schema_definition_helper(spec, name, schema, **kwargs):
    """Definition helper that allows using a marshmallow
    :class:`Schema <marshmallow.Schema>` to provide OpenAPI
    metadata.

    :param type|Schema schema: A marshmallow Schema class or instance.
    """

    schema_ref = get_schema_ref(schema)
    schema_instance = get_schema_instance(schema)

    # Store registered refs, keyed by Schema class
    plug = spec.plugins[NAME]
    if 'refs' not in plug:
        plug['refs'] = {}
    plug['refs'][schema_ref] = name

    json_schema = swagger.schema2jsonschema(schema_instance, spec=spec, name=name)

    # Auto reference schema if spec.schema_name_resolver
    if spec and spec.schema_name_resolver:
        inspect_schema_for_auto_referencing(spec, schema_instance)

    return json_schema


def schema_path_helper(spec, view=None, **kwargs):
    """Path helper that allows passing a Schema as a response. Responses can be
    defined in a view's docstring.
    ::

        from pprint import pprint

        from my_app import Users, UserSchema

        class UserHandler:
            def get(self, user_id):
                '''Get a user endpoint.
                ---
                description: Get a user
                responses:
                    200:
                        description: A user
                        schema: UserSchema
                '''
                user = Users.get(id=user_id)
                schema = UserSchema()
                return schema.dumps(user)

        urlspec = (r'/users/{user_id}', UserHandler)
        spec.add_path(urlspec=urlspec)
        pprint(spec.to_dict()['paths'])
        # {'/users/{user_id}': {'get': {'description': 'Get a user',
        #                               'responses': {200: {'description': 'A user',
        #                                                   'schema': {'$ref': '#/definitions/User'}}}}}}

    ::

        from pprint import pprint

        from my_app import Users, UserSchema

        class UsersHandler:
            def get(self):
                '''Get users endpoint.
                ---
                description: Get a list of users
                responses:
                    200:
                        description: A list of user
                        schema:
                            type: array
                            items: UserSchema
                '''
                users = Users.all()
                schema = UserSchema(many=True)
                return schema.dumps(users)

        urlspec = (r'/users', UsersHandler)
        spec.add_path(urlspec=urlspec)
        pprint(spec.to_dict()['paths'])
        # {'/users': {'get': {'description': 'Get a list of users',
        #                     'responses': {200: {'description': 'A list of users',
        #                                         'schema': {'type': 'array',
        #                                                    'items': {'$ref': '#/definitions/User'}}}}}}}

    """
    operations = (
        kwargs.get('operations') or
        (view and load_operations_from_docstring(view.__doc__))
    )
    if not operations:
        return
    operations = operations.copy()
    return Path(operations=operations)


def schema_operation_resolver(spec, operations, **kwargs):
    for operation in operations.values():
        if not isinstance(operation, dict):
            continue
        if 'parameters' in operation:
            operation['parameters'] = resolve_parameters(spec, operation['parameters'])
        for response in operation.get('responses', {}).values():
            if 'schema' in response:
                response['schema'] = resolve_schema_dict(spec, response['schema'])


def resolve_parameters(spec, parameters):
    resolved = []
    for parameter in parameters:
        if not isinstance(parameter.get('schema', {}), dict):
            schema_cls = resolve_schema_cls(parameter['schema'])
            if issubclass(schema_cls, marshmallow.Schema) and 'in' in parameter:
                resolved += swagger.schema2parameters(
                    schema_cls, default_in=parameter['in'], spec=spec)
                continue
        resolved.append(parameter)
    return resolved


def resolve_schema_dict(spec, schema, dump=True, use_instances=False):
    if isinstance(schema, dict):
        if (schema.get('type') == 'array' and 'items' in schema):
            schema['items'] = resolve_schema_dict(spec, schema['items'], use_instances=use_instances)
        return schema
    plug = spec.plugins[NAME] if spec else {}
    if isinstance(schema, marshmallow.Schema) and use_instances:
        schema_cls = schema
    else:
        schema_cls = resolve_schema_cls(schema)

    schema_ref = get_schema_ref(schema)
    if not schema_ref:
        schema_ref = schema_cls

    if schema_ref in plug.get('refs', {}):
        ref_schema = {'$ref': '#/definitions/{0}'.format(plug['refs'][schema_ref])}
        if getattr(schema, 'many', False):
            return {
                'type': 'array',
                'items': ref_schema,
            }
        return ref_schema
    if not isinstance(schema, marshmallow.Schema):
        schema = schema_cls
    return swagger.schema2jsonschema(schema, spec=spec, dump=dump)


def resolve_schema_cls(schema):
    if isinstance(schema, type) and issubclass(schema, marshmallow.Schema):
        return schema
    if isinstance(schema, marshmallow.Schema):
        return type(schema)
    return marshmallow.class_registry.get_class(schema)


def setup(spec):
    """Setup for the marshmallow plugin."""
    spec.register_definition_helper(schema_definition_helper)
    spec.register_path_helper(schema_path_helper)
    spec.register_operation_helper(schema_operation_resolver)
