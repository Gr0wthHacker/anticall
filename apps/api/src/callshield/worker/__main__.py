"""`python -m callshield.worker` starts the DB-queue worker."""

from callshield.worker.runner import run_forever

if __name__ == "__main__":
    run_forever()
