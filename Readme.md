# Nightjetter

Nightjetter is a python (3.10+) wrapper over the unofficial API

## Usage

* Fork locally and make sure to use the `feat/aws-chalice` branch

* Adapt your passengers to your desired searches in the `app.py` file, mapping them in the `PASSENGERS` dict

* Adapt `CONNECTIONS`, the list of connections you wanna get tickets for. You may also adapt the value of the decorator `@app.schedule(Cron())`

### Run Locally

* Install `pip install -r requirements.txt`

* Run `python app.py`

### Run on AWS Lambda through chalice

* Install via `pip install chalice`

* Set-up your AWS credentials as described in https://github.com/aws/chalice?tab=readme-ov-file#credentials

* Copy the chalice config `cp .chalice/config.json.example .chalice/config.json`

* Set the proper values to the environment variables `SENDGRID*` in the chalice config file if you want to get alerted per mail when new tickets are available, using the free tier of https://www.sendgrid.com

* Run `python env_var.py` and copy the string of connections as the value of the environment variable `CONNECTIONS_STR` in `.chalice/config.json`

* Deploy to AWS Lambda using `chalice deploy`

* Check logs (after the lambda schedule triggered a run) via `chalice logs -n lambda_func`
