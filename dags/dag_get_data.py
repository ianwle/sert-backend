import datetime
import pendulum

import requests
import pandas as pd
from airflow.decorators import dag, task
from airflow.providers.postgres.hooks.postgres import PostgresHook
from airflow.operators.python_operator import BranchPythonOperator, PythonOperator
from airflow.operators.dummy import DummyOperator
from airflow.utils.trigger_rule import TriggerRule

from nlp_methods import *

import csv
import sys
import extractor

MAGNITUDE_LIMIT = 0

@dag(
    schedule_interval="0 0 * * *",
    start_date=pendulum.datetime(2021, 1, 1, tz="UTC"),
    catchup=False,
    dagrun_timeout=datetime.timedelta(minutes=60),
)
def get_usgs_feed():
    @task
    def get_data():
        data_path = "/opt/airflow/dags/files/usgs_feed.csv"

        feed = extractor.extract_usgs_feed_all()
        processed_feed = []

        for entry in feed.entries:
            try:
                processed_feed.append(extractor.extract_usgs_details(entry))
            except Exception as e:
                # Error if return type is None, remove.
                continue

        with open(data_path, "w", newline="") as f:
            title = list(processed_feed[0].keys())
            cw = csv.DictWriter(f, title, delimiter=',',
                                quotechar='\"', quoting=csv.QUOTE_MINIMAL)
            cw.writeheader()
            cw.writerows(processed_feed)

        postgres_hook = PostgresHook(postgres_conn_id="LOCAL")
        conn = postgres_hook.get_conn()
        cur = conn.cursor()
        with open(data_path, "r") as file:
            cur.copy_expert(
                "COPY \"Events_temp\" FROM STDIN WITH CSV HEADER DELIMITER AS ',' QUOTE '\"'",
                file,
            )
        conn.commit()

    def _merge_data():
        query = """
                DELETE FROM "Events" e
                USING "Events_temp" et
                WHERE e."URL" = et."URL";

                INSERT INTO "Events"
                SELECT *
                FROM "Events_temp";
                """
        try:
            postgres_hook = PostgresHook(postgres_conn_id="LOCAL")
            conn = postgres_hook.get_conn()
            cur = conn.cursor()
            cur.execute(query)
            conn.commit()
            return 0
        except Exception as e:
            return 1

    merge_data = PythonOperator(
        task_id="merge_data",
        python_callable=_merge_data
    )
    
    end_process_dummy = DummyOperator(task_id="end_process_dummy")     

    def _get_twitter_and_news():
        try:
            postgres_hook = PostgresHook(postgres_conn_id="LOCAL")
            conn = postgres_hook.get_conn()
            cur = conn.cursor()
            cur.execute("SELECT * FROM \"Events_temp\"") # select all the new events
            records = cur.fetchall()

            for record in records:
                print(record)
                # access the magnitude from the tuple data type
                if record[-3] >= MAGNITUDE_LIMIT and record[-4] == "United States":
                    # launch news search
                    # launch twitter search
                    print(record)
            return 0
        except Exception as e:
            return 1

    get_twitter_and_news = PythonOperator(
        task_id="get_twitter_and_news",
        python_callable=_get_twitter_and_news,
        trigger_rule=TriggerRule.ONE_SUCCESS
    )

    def _get_news_data():
        pass
       
    def _clear_data():
        print("LOG: Clearing data in temporary Events table.")
        try:
            postgres_hook = PostgresHook(postgres_conn_id="LOCAL")
            conn = postgres_hook.get_conn()
            cur = conn.cursor()
            cur.execute("DELETE FROM \"Events_temp\"")
            conn.commit()
            return 0
        except Exception as e:
            return 1

    clear_data = PythonOperator(
        task_id="clear_data",
        python_callable=_clear_data,
        trigger_rule=TriggerRule.NONE_FAILED_MIN_ONE_SUCCESS
    )

    def compare_data(**kwargs):
        query = """
            SELECT * FROM "Events_temp" EXCEPT SELECT * FROM "Events";
            """
        postgres_hook = PostgresHook(postgres_conn_id="LOCAL")
        conn = postgres_hook.get_records(query)
        print(conn)
        
        if len(conn) > 0:
            return "merge_data"
        else:
            return "end_process_dummy"
    
    branch_on_check = BranchPythonOperator(
        task_id="branch_on_check",
        python_callable=compare_data
        )


    get_data() >> branch_on_check >> [merge_data, end_process_dummy] >> get_twitter_and_news >> clear_data

dag = get_usgs_feed()