"""A narrow impeller classifier; intentionally separate from the camera detector."""
import hashlib
import json
from pathlib import Path

import torch
from transformers import AutoImageProcessor,AutoModelForImageClassification

ROOT=Path(__file__).resolve().parent


class CastingClassifier:
    def __init__(self,device='cpu'):
        directory=ROOT/'experimental/casting-vit'
        manifest=json.loads((directory/'source-manifest.json').read_text())
        for entry in manifest['files']:
            if hashlib.sha256((directory/entry['file']).read_bytes()).hexdigest()!=entry['downloadedSha256']:
                raise ValueError('Modified classifier asset: '+entry['file'])
        self.device=device
        torch.set_num_threads(4)
        self.processor=AutoImageProcessor.from_pretrained(str(directory),local_files_only=True,trust_remote_code=False,use_fast=False)
        self.model=AutoModelForImageClassification.from_pretrained(str(directory),local_files_only=True,trust_remote_code=False,use_safetensors=True).eval().to(device)
        self.labels=[self.model.config.id2label[i] for i in range(2)]
        if set(self.labels)!={'defective','ok'}:raise ValueError('Unexpected casting classes')
        self.version='casting-vit-'+manifest['revision'][:12]

    def predict(self,images):
        inputs=self.processor(images=images,return_tensors='pt').to(self.device)
        with torch.inference_mode():scores=self.model(**inputs).logits.softmax(-1).cpu().tolist()
        return [{self.labels[i]:float(row[i]) for i in range(2)} for row in scores]
