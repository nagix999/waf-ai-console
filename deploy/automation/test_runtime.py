"""Mock-only comparison of approved Gateway models and running/stopped state."""
from copy import deepcopy
import unittest

from .common import DeployError
from .runtime import assert_runtime_matches
from . import test_team as fixtures


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.TeamTests("test_discovery_is_private_read_only_and_gateway_only")
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.runner = self.fixture.runner
        self.model = deepcopy(self.fixture.model)
        self.model["services"]["gateway"].pop("build")
        self.model["services"]["gateway"].pop("depends_on")
        self.model["services"]["gateway"]["image"] = fixtures.BASE_IMAGE
        self.fixture.runtime["Id"] = fixtures.GATEWAY_ID
        self.fixture.runtime["HostConfig"]["PortBindings"] = deepcopy(self.fixture.runtime["NetworkSettings"]["Ports"])
        self.fixture.runtime["HostConfig"]["NetworkMode"] = self.model["name"] + "_edge"
        self.fixture.runtime["NetworkSettings"]["Networks"][self.model["name"] + "_edge"]["IPAMConfig"] = {"IPv4Address": "172.29.3.10"}

    def verify(self, **kwargs):
        return assert_runtime_matches(self.runner, fixtures.GATEWAY_ID, self.model, **kwargs)

    def stopped(self):
        self.fixture.runtime["State"] = {"Status": "exited", "Running": False}
        self.fixture.runtime["NetworkSettings"]["Ports"] = {}
        for value in self.fixture.runtime["NetworkSettings"]["Networks"].values():
            value["IPAddress"] = ""
            value["NetworkID"] = ""

    def test_matching_runtime_only_uses_inspection(self):
        self.verify()
        self.assertTrue(all(call[0] == "docker" and call[2] == "inspect" for call in self.runner.calls))

    def test_stopped_candidate_requires_explicit_mode_and_retained_fixed_ip(self):
        self.stopped()
        with self.assertRaises(DeployError):
            self.verify()
        self.verify(allow_stopped=True)
        self.fixture.runtime["NetworkSettings"]["Networks"][self.model["name"] + "_edge"]["IPAMConfig"] = None
        with self.assertRaisesRegex(DeployError, "stopped_fixed_ip_unverifiable"):
            self.verify(allow_stopped=True)

    def test_stopped_candidate_still_rejects_changed_mounts_and_host_ports(self):
        self.stopped()
        original = deepcopy(self.fixture.runtime)
        for mutate in (
            lambda: self.fixture.runtime["Mounts"][0].update(Source="/tmp/unapproved-certificate"),
            lambda: self.fixture.runtime["HostConfig"]["PortBindings"]["3030/tcp"][0].update(HostIp="0.0.0.0"),
        ):
            self.fixture.runtime = deepcopy(original)
            mutate()
            with self.assertRaises(DeployError):
                self.verify(allow_stopped=True)

    def test_same_image_does_not_authorize_manual_configuration_changes(self):
        original = deepcopy(self.fixture.runtime)
        mutations = (
            lambda: self.fixture.runtime["Config"]["Env"].append("PRIVATE_VALUE=DO_NOT_PRINT"),
            lambda: self.fixture.runtime["Config"].update(User="root"),
            lambda: self.fixture.runtime["Config"].update(Cmd=["sh", "-c", "fixture"]),
            lambda: self.fixture.runtime["HostConfig"].update(ReadonlyRootfs=False),
            lambda: self.fixture.runtime["HostConfig"].update(Privileged=True),
            lambda: self.fixture.runtime["HostConfig"].update(ExtraHosts=["api:203.0.113.9"]),
            lambda: self.fixture.runtime["HostConfig"].update(PidMode="host"),
            lambda: self.fixture.runtime["HostConfig"].update(CapAdd=["NET_ADMIN"]),
            lambda: self.fixture.runtime["HostConfig"].update(SecurityOpt=[]),
            lambda: self.fixture.runtime["Mounts"][0].update(RW=True),
            lambda: self.fixture.runtime["NetworkSettings"]["Networks"].update(foreign={}),
            lambda: self.fixture.runtime["NetworkSettings"]["Networks"][self.model["name"] + "_edge"].update(IPAddress="172.29.3.90"),
        )
        for mutate in mutations:
            self.fixture.runtime = deepcopy(original)
            mutate()
            with self.assertRaises(DeployError) as caught:
                self.verify()
            self.assertNotIn("DO_NOT_PRINT", str(caught.exception))

    def test_image_and_project_identity_are_checked(self):
        self.fixture.runtime["Image"] = fixtures.MANAGED_IMAGE
        with self.assertRaisesRegex(DeployError, "identity_changed"):
            self.verify()
        self.fixture.runtime["Image"] = fixtures.BASE_IMAGE
        self.fixture.runtime["Config"]["Labels"]["com.docker.compose.project"] = "different-project"
        with self.assertRaisesRegex(DeployError, "project_changed"):
            self.verify()

    def test_model_image_must_be_content_pinned(self):
        self.model["services"]["gateway"]["image"] = "team-workspace-gateway:production"
        with self.assertRaisesRegex(DeployError, "image_not_pinned"):
            self.verify()

    def test_unhealthy_running_candidate_only_allowed_for_recovery_comparison(self):
        self.fixture.runtime["State"]["Health"]["Status"] = "unhealthy"
        with self.assertRaises(DeployError):
            self.verify()
        self.verify(allow_stopped=True)

    def test_restarting_candidate_needs_verifiable_desired_endpoints(self):
        self.stopped()
        self.fixture.runtime["State"] = {"Status": "restarting", "Running": True}
        self.verify(allow_stopped=True)

    def test_paused_or_dead_runtime_is_not_automatically_overwritten(self):
        for status in ("paused", "dead", "removing"):
            self.fixture.runtime["State"] = {"Status": status, "Running": True}
            with self.assertRaises(DeployError):
                self.verify(allow_stopped=True)


if __name__ == "__main__":
    unittest.main()
