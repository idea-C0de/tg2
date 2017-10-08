# -*- coding: utf-8 -*-
from tg.appwrappers.errorpage import ErrorPageApplicationWrapper
from ..base import ConfigurationStep, BeforeConfigConfigurationAction


class ErrorPagesConfigurationStep(ConfigurationStep):
    """

    """
    id = 'error_pages'

    def get_defaults(self):
        return {
            'errorpage.enabled': True,
            'errorpage.status_codes': [403, 404]
        }

    def get_actions(self):
        return (
            BeforeConfigConfigurationAction(self._configure_error_pages),
        )

    def on_bind(self, configurator):
        configurator.register_application_wrapper(ErrorPageApplicationWrapper, after=True)

    def _configure_error_pages(self, conf, app):
        if conf.get('auth_backend') is None and 401 not in conf['errorpage.status_codes']:
            # If there's no auth backend configured which traps 401
            # responses we redirect those responses to a nicely
            # formatted error page
            conf['errorpage.status_codes'] = list(conf['errorpage.status_codes']) + [401]
