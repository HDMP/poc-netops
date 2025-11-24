from nautobot.apps import jobs

name = "Examples"                 # Grouping shown in the UI

class HelloWorldJob(jobs.Job):
    class Meta:
        name = "Hello World"      # Job name

    who = jobs.StringVar(         # One user‑supplied variable
        description="Whom should we greet?",
        default="world",
    )

    def run(self, *, who):
        self.logger.info("Hello, %s!", who)  # This is the logic that runs when the Job is executed

jobs.register_jobs(HelloWorldJob) # <- required in Nautobot 2.x
