"""
Copyright 2017 Google Inc.
Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.

-------------------------------------------------------------------------------

Dataflow Main

A dataflow pipeline to ingest the Wikipedia dump from 7zipped xml files to json.

Run with:

python dataflow_main.py --setup_file ./setup.py 

"""

from __future__ import absolute_import

import argparse
import logging
import subprocess
import json
import sys
import zlib
import copy
from os import path
import xml.sax
from ingest_utils import wikipedia_revisions_ingester as wiki_ingester

import apache_beam as beam
from apache_beam.options.pipeline_options import PipelineOptions
from apache_beam.options.pipeline_options import SetupOptions
from apache_beam.io import ReadFromText
from apache_beam.io import WriteToText
from apache_beam.pipeline import AppliedPTransform
from apache_beam.io.gcp import bigquery #WriteToBigQuery

THERESHOLD = 10485760 

def run(known_args, pipeline_args):
  """Main entry point; defines and runs the wordcount pipeline."""

  pipeline_args.extend([
    '--runner=DataflowRunner',
    '--project=wikidetox-viz',
    '--staging_location=gs://wikidetox-viz-dataflow/staging',
    '--temp_location=gs://wikidetox-viz-dataflow/tmp',
    '--job_name=yiqing-ingest-job-truncated-content-run-in-batch',
    '--num_workers=90',
  ])

  pipeline_options = PipelineOptions(pipeline_args)
  pipeline_options.view_as(SetupOptions).save_main_session = True
  if not(known_args.batchno == None):
     known_args.input = 'gs://wikidetox-viz-dataflow/input_lists/7z_file_list_batched_%d'%(known_args.batchno)
     known_args.table = 'wikidetox-viz:wikidetox_conversations.ingested_conversations_batch_%d'%(known_args.batchno)   
     print('Running batch %d'%(known_args.batchno))    
  with beam.Pipeline(options=pipeline_options) as p:
    pcoll = (p | ReadFromText(known_args.input)
                   | beam.ParDo(WriteDecompressedFile())
                   | beam.io.Write(bigquery.BigQuerySink(known_args.table, schema=known_args.schema, validate = True)))

def truncate_content(s):
    dic = json.loads(s) 
    dic['truncated'] = False
    dic['no_records'] = 1
    dic['record_index'] = 0
    if sys.getsizeof(s) > THERESHOLD:
       l = len(dic['text'])
       dic['truncated'] = True
       dic['no_records'] = 2
       dic1 = copy.deepcopy(dic)
       dic1['text'] = dic1['text'][:l/2]
       dic1['record_index'] = 0
       dic2 = copy.deepcopy(dic)
       dic2['record_index'] = 1
       dic2['text'] = dic2['text'][l/2:]
       return [dic1, dic2]
    return [dic]

class WriteDecompressedFile(beam.DoFn):
  def process(self, element):
    logging.info('USERLOG: Working on %s' % element)
    chunk_name = element

    in_file_path = path.join('gs://wikidetox-viz-dataflow/raw-downloads/', chunk_name)

    logging.info('USERLOG: Running gsutil %s ./' % in_file_path)
    cp_local_cmd = (['gsutil', 'cp', in_file_path, './'])
    subprocess.call(cp_local_cmd)

    logging.info('USERLOG: Running ingestion process on %s' % chunk_name)
    ingestion_cmd = ['python2', '-m', 'ingest_utils.run_ingester', '-i', chunk_name]
    ingest_proc = subprocess.Popen(ingestion_cmd, stdout=subprocess.PIPE, bufsize = 4096)
    # Yiqing's comment: stdout is the result of the ingestion
    cnt = 0
    maxsize = 0
    for i, line in enumerate(ingest_proc.stdout):
      ret = truncate_content(line)
      for r in ret:
          yield r
      maxsize = max(maxsize, sys.getsizeof(line))
      cnt += 1
    logging.info('USERLOG: File %s complete! %s lines emitted, maxsize: %d' % (chunk_name, cnt, maxsize))

if __name__ == '__main__':
  logging.getLogger().setLevel(logging.INFO)
  parser = argparse.ArgumentParser()
  parser.add_argument('--batchno', 
                      dest='batchno',
                      default=None,
                      type=int, 
                      help='If you want to run the input dumps in batch, pick a batch number to run')
  parser.add_argument('--input',
                      dest='input',
                      default='gs://wikidetox-viz-dataflow/input_lists/7z_file_list_short_10.txt',
                      help='Input file to process.')
  # Destination BigQuery Table
  schema = 'sha1:STRING,user_id:STRING,format:STRING,user_text:STRING,timestamp:STRING,text:STRING,page_title:STRING,model:STRING,page_namespace:STRING,page_id:STRING,rev_id:STRING,comment:STRING, user_ip:STRING, truncated:BOOLEAN,no_records:INTEGER,record_index:INTEGER'
  parser.add_argument('--table',
                      dest='table',
                      default='wikidetox-viz:wikidetox_conversations.ingested_conversations',
                      help='Output table to write results to.')
  parser.add_argument('--schema',
                      dest='schema',
                      default=schema,
                      help='Output table schema.')

  known_args, pipeline_args = parser.parse_known_args()
  run(known_args, pipeline_args)

