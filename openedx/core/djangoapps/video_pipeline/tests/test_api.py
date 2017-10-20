"""
Tests for Video Pipeline api utils.
"""
import ddt
from mock import Mock, patch

from django.test.testcases import TestCase
from slumber.exceptions import HttpClientError

from student.tests.factories import UserFactory

from openedx.core.djangoapps.video_pipeline.api import update_3rd_party_transcription_service_credentials
from openedx.core.djangoapps.video_pipeline.tests.mixins import VideoPipelineIntegrationMixin


@ddt.ddt
class TestAPIUtils(VideoPipelineIntegrationMixin, TestCase):
    """
    Tests for API Utils.
    """
    def setUp(self):
        self.pipeline_integration = self.create_video_pipeline_integration()
        self.user = UserFactory(username=self.pipeline_integration.service_username)

    def test_update_transcription_service_credentials_with_integration_disabled(self):
        """
        Test updating the credentials when service integration is disabled.
        """
        self.pipeline_integration.enabled = False
        self.pipeline_integration.save()
        is_updated = update_3rd_party_transcription_service_credentials()
        self.assertFalse(is_updated)

    def test_update_transcription_service_credentials_with_unknown_user(self):
        """
        Test updating the credentials when expected service user is not registered.
        """
        self.pipeline_integration.service_username = 'non_existent_user'
        self.pipeline_integration.save()
        is_updated = update_3rd_party_transcription_service_credentials()
        self.assertFalse(is_updated)

    @ddt.data(
        {
            'username': 'Jason_cielo_24',
            'api_key': '12345678',
        },
        {
            'api_key': '12345678',
            'api_secret': '11111111',
        }
    )
    @patch('openedx.core.djangoapps.video_pipeline.api.log')
    @patch('openedx.core.djangoapps.video_pipeline.utils.EdxRestApiClient')
    def test_update_transcription_service_credentials(self, credentials_payload, mock_client, mock_logger):
        """
        Tests that the update transcription service credentials api util works as expected.
        """
        # Mock the post request
        mock_credentials_endpoint = mock_client.return_value.api.transcript_credentials
        # Try updating the transcription service credentials
        is_updated = update_3rd_party_transcription_service_credentials(**credentials_payload)

        mock_credentials_endpoint.post.assert_called_with(credentials_payload)
        # Making sure log.exception is not called.
        self.assertFalse(mock_logger.exception.called)
        self.assertTrue(is_updated)

    @patch('openedx.core.djangoapps.video_pipeline.api.log')
    @patch('openedx.core.djangoapps.video_pipeline.utils.EdxRestApiClient')
    def test_update_transcription_service_credentials_exceptions(self, mock_client, mock_logger):
        """
        Tests that the update transcription service credentials logs the exception occurring
        during communication with edx-video-pipeline.
        """
        # Mock the post request
        mock_credentials_endpoint = mock_client.return_value.api.transcript_credentials
        mock_credentials_endpoint.post = Mock(side_effect=HttpClientError(content='invalid_credentials'))
        # try updating the transcription service credentials
        credentials_payload = {'invalid_param': 'invalid_value'}
        is_updated = update_3rd_party_transcription_service_credentials(**credentials_payload)

        mock_credentials_endpoint.post.assert_called_with(credentials_payload)
        self.assertFalse(is_updated)
        mock_logger.exception.assert_called_with(
            '[video-pipeline-service] Unable to update transcript credentials -- response -- %s',
            'invalid_credentials'
        )
