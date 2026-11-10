"""The conformance suite of SPEC §10 against the Temporal stub (§5.2, §5.3), inside one activity on a worker of the
time-skipping test server (`WorkflowEnvironment.start_time_skipping()`, which downloads its test server once).

Before the suite, three workflows give the engine parts the sentences assert on (`engine=`): a decorated body called
in workflow code (A4), a held Outcome turned into a non-retryable error by `raise_if_held` (T5), and a crash after
the commit callable ran, retried by Temporal (A6). Skipped where temporalio is not installed."""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_conformance import no_sockets  # noqa: E402

try:
    import temporalio  # noqa: F401
    HAVE_TEMPORAL = True
except ImportError:
    HAVE_TEMPORAL = False

EXPECTED_SKIPS = {"A5", "A8"}


class TemporalImport(unittest.TestCase):
    def test_the_module_imports_without_temporalio(self):
        from as_agreed.adapters import temporal as tmod
        self.assertTrue(callable(tmod.raise_if_held))
        if not HAVE_TEMPORAL:
            with self.assertRaises(ImportError) as cm:
                tmod.TemporalAdapter()
            self.assertIn("pip install temporalio", str(cm.exception))


if HAVE_TEMPORAL:  # workflows are defined at module level (Temporal refuses local classes)
    import asyncio
    import concurrent.futures
    import uuid
    from datetime import timedelta

    from temporalio import activity, workflow
    from temporalio.client import WorkflowFailureError
    from temporalio.common import RetryPolicy
    from temporalio.testing import WorkflowEnvironment
    from temporalio.worker import UnsandboxedWorkflowRunner, Worker

    import as_agreed.sdk as ab
    from as_agreed.adapters.base import Outcome
    from as_agreed.adapters.temporal import TemporalAdapter, raise_if_held, temporal_adapter
    from as_agreed.conformance import COMMIT_BAD, COMMIT_GOOD, ORDER, RESEARCH, SCHEMA, commitments, run_suite

    TA = TemporalAdapter()
    SEEN = {"bodies": [], "payments": [], "attempts": [], "workflow_bodies": []}
    OPTS = dict(start_to_close_timeout=timedelta(seconds=120))
    RETRY = RetryPolicy(initial_interval=timedelta(milliseconds=100), maximum_attempts=3)

    @activity.defn(name="register_order")
    def register_order() -> dict:
        return TA.register(ORDER, commitments(), run=[])

    def research_body(inputs: str) -> str:
        return RESEARCH
    research_step = TA.step("research")(research_body)

    @activity.defn(name="research")
    def research_act(inputs: str, records: list) -> Outcome:
        return research_step(inputs, records)

    def record_payment(outputs, *, idempotency_key):
        SEEN["payments"].append(idempotency_key)

    def bad_body(inputs: str) -> dict:
        SEEN["bodies"].append("bad")
        return dict(COMMIT_BAD)
    bad_step = TA.step("commit", commit=record_payment, schema=SCHEMA, retries=1)(bad_body)

    @activity.defn(name="commit_bad")
    def commit_bad(inputs: str, records: list) -> Outcome:
        SEEN["attempts"].append(("bad", activity.info().attempt))
        return bad_step(inputs, records)

    def pay(outputs, *, idempotency_key):
        SEEN["payments"].append(idempotency_key)
        if activity.info().attempt == 1:
            raise RuntimeError("the worker crashed after the payment")

    def good_body(inputs: str) -> dict:
        SEEN["bodies"].append("good")
        return dict(COMMIT_GOOD)
    good_step = TA.step("commit", commit=pay, schema=SCHEMA)(good_body)

    @activity.defn(name="commit_good")
    def commit_good(inputs: str, records: list) -> Outcome:
        SEEN["attempts"].append(("good", activity.info().attempt))
        return good_step(inputs, records)

    @activity.defn(name="suite")
    def suite(engine: dict) -> list:
        results = run_suite(temporal_adapter, guard=no_sockets(), engine=engine)
        return [{"id": r.id, "status": r.status, "message": r.message} for r in results]

    def in_workflow_body(inputs: str) -> str:
        SEEN["workflow_bodies"].append(inputs)
        return RESEARCH
    in_workflow_step = ab.step("research", adapter=TA)(in_workflow_body)

    @workflow.defn(name="Refusal")
    class Refusal:
        @workflow.run
        async def run(self) -> dict:
            try:
                in_workflow_step(ORDER)
            except ab.WorkflowContextError as e:
                return {"error": type(e).__name__, "body_ran": bool(SEEN["workflow_bodies"])}
            return {"error": None, "body_ran": bool(SEEN["workflow_bodies"])}

    @workflow.defn(name="Flow")
    class Flow:
        @workflow.run
        async def run(self, which: str) -> dict:
            records = (await workflow.execute_activity(register_order, **OPTS))["records"]
            r = await workflow.execute_activity(research_act, args=[ORDER, records], result_type=Outcome, **OPTS)
            records = records + r.records
            out = await workflow.execute_activity("commit_" + which, args=[RESEARCH, records], result_type=Outcome,
                                                  retry_policy=RETRY, **OPTS)
            raise_if_held(out)
            return {"passed": out.passed, "code": out.code}

    @workflow.defn(name="Suite")
    class Suite:
        @workflow.run
        async def run(self, engine: dict) -> list:
            return await workflow.execute_activity(suite, engine, start_to_close_timeout=timedelta(seconds=300))

    async def _main():
        env = await WorkflowEnvironment.start_time_skipping()
        facts = {"environment": f"the time-skipping test server (temporalio {temporalio.__version__})"}

        def wid():
            return "k2-" + uuid.uuid4().hex[:8]
        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
                async with Worker(env.client, task_queue="k2", workflows=[Refusal, Flow, Suite],
                                  activities=[register_order, research_act, commit_bad, commit_good, suite],
                                  activity_executor=pool, workflow_runner=UnsandboxedWorkflowRunner()):
                    ref = await env.client.execute_workflow(Refusal.run, id=wid(), task_queue="k2")
                    facts["workflow_refusal"] = dict(ref, environment=facts["environment"])
                    try:
                        await env.client.execute_workflow(Flow.run, "bad", id=wid(), task_queue="k2")
                        facts["raise_if_held"] = {"type": None}
                    except WorkflowFailureError as e:
                        cause = e.cause
                        facts["raise_if_held"] = {"type": getattr(cause, "type", None),
                                                  "non_retryable": getattr(cause, "non_retryable", None),
                                                  "activity_attempts": sum(1 for w, _ in SEEN["attempts"] if w == "bad"),
                                                  "bodies": SEEN["bodies"].count("bad")}
                    paid = len(SEEN["payments"])
                    good = await env.client.execute_workflow(Flow.run, "good", id=wid(), task_queue="k2")
                    facts["resume"] = {"passed": good["passed"], "bodies": SEEN["bodies"].count("good"),
                                       "payments": len(SEEN["payments"]) - paid,
                                       "activity_attempts": sum(1 for w, _ in SEEN["attempts"] if w == "good")}
                    results = await env.client.execute_workflow(Suite.run, facts, id=wid(), task_queue="k2")
        finally:
            await env.shutdown()
        return facts, results


def _engine_run():
    """Start the test server and a worker; return (engine facts, suite results)."""
    return asyncio.run(_main())


@unittest.skipUnless(HAVE_TEMPORAL, "temporalio is not installed: pip install temporalio")
class TemporalConformance(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.facts, cls.results = _engine_run()

    def test_the_engine_parts(self):
        self.assertEqual(self.facts["workflow_refusal"]["error"], "WorkflowContextError")
        self.assertFalse(self.facts["workflow_refusal"]["body_ran"])
        self.assertEqual(self.facts["raise_if_held"]["type"], "DriftHold")
        self.assertTrue(self.facts["raise_if_held"]["non_retryable"])
        self.assertEqual(self.facts["resume"], {"passed": True, "bodies": 1, "payments": 1, "activity_attempts": 2})

    def test_every_shared_sentence_and_the_temporal_sentences_pass_inside_an_activity(self):
        from as_agreed.conformance import SENTENCES
        failed = [r for r in self.results if r["status"] == "fail"]
        self.assertEqual(failed, [], "\n" + "\n".join(f"{r['id']}: {r['message']}" for r in failed))
        self.assertEqual({r["id"] for r in self.results if r["status"] == "skip"}, EXPECTED_SKIPS)
        self.assertEqual(sum(1 for r in self.results if r["status"] == "pass"), len(SENTENCES) - len(EXPECTED_SKIPS))
        by_id = {r["id"]: r for r in self.results}
        self.assertIn("socket guard", by_id["K13"]["message"])
        self.assertIn("time-skipping", by_id["A6"]["message"])

    def test_a_step_outside_an_activity_is_refused(self):
        import as_agreed.sdk as ab
        from as_agreed.adapters.temporal import TemporalAdapter
        ta = TemporalAdapter()

        @ab.step("research", adapter=ta)
        def body(inputs: str) -> str:
            return inputs
        with self.assertRaises(ValueError) as cm:
            body("x")
        self.assertIn("inside an activity", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
