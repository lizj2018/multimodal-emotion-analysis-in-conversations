import torch
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
from torch.utils.data import Dataset, DataLoader
from torchvision import transforms, utils
from PIL import Image
import os
import cv2
from scipy.io import wavfile
import pickle
from models.visual_features import detect_faces_mtcnn

def video_to_tensor(video_file):
    """ Converts a mp4 file into a pytorch tensor"""

    cap = cv2.VideoCapture(video_file)
    frameCount = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    frameWidth = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    frameHeight = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

    buf = np.empty((frameCount, frameHeight, frameWidth, 3), np.dtype('uint8'))

    fc = 0
    ret = True

    while (fc < frameCount  and ret):
        ret, buf[fc] = cap.read()
        fc += 1

    cap.release()
    return torch.tensor(buf)

class Dialogue(object):
    """
    Class for representing a dialogue as a list of utterances
    """
    def __init__(self, id, utterances, visual_features=False):
        self.dialogue_id = id
        self.utterances = utterances
        self.visual_features = visual_features
        self.reparameterize_speakers()

    def reparameterize_speakers(self):
        """
        Method for reparameterizing speakers to the specific dialogue. Eg:
            speaker_ids (1, 3, 4) -> (0, 1, 2)
        """
        speaker_map = {}
        id = 0
        for utterance in self.utterances:
            if utterance.speaker not in speaker_map.keys():
                speaker_map[utterance.speaker] = id
                id += 1

        self.dialogue_speaker_map = speaker_map

    def get_transcripts(self):
        """
        Method returns a list of text transcripts for each utterance
        """
        return [utterance.get_transcript() for utterance in self.utterances]

    def get_videos(self):
        """
        Method returns a list of raw video tensors for each utterance
        """
        #videos = [utterance.load_video() for utterance in self.utterances]
        return [utterance.load_video() for utterance in self.utterances]

    def get_visual_features(self):
        """
        Method returns a list of visual features
        """

        features = [utterance.get_cached_visual_features() for utterance in self.utterances]
        return features

    def get_audios(self):
        """
        Method returns a list of audio embeddings for each utterance
        """
        return [utterance.load_audio() for utterance in self.utterances]

    def get_speakers(self):
        """
        Method returns a list of speaker ids for every utterance in the dialogue.
        Speaker id's are mapped to be relative within the dialogue ie. all id's
        are [0, n] where n is the number of different speakers in the dialogue
        """
        # map speaker ids to relative id within the dialogue        
        return torch.LongTensor([self.dialogue_speaker_map[utterance.speaker] for utterance in self.utterances])

    def get_labels(self):
        """
        Method returns the labels as a tuple of lists. Each list contains the
        integer id corresponding to either the emotion or sentiment. The returned
        data is in the following format:

        ([emotion_id], [sentiment_id])
        """
        emotions = [utterance.emotion for utterance in self.utterances]
        sentiment = [utterance.sentiment for utterance in self.utterances]
        return (emotions, sentiment)

    def get_inputs(self):
        """
        Method returns all the inputs as a tuple of list. Each list corresponds
        to the input of a specific modality for each utterance. The returned
        data is in the following format:

        if self.visual_features is true:

            returns video is a list of tensors representing faces detected for each utterances 

        else 

            video returns the raw video tensors

        ([transcripts], [video], [audio_embeddings], [speakers])
        """
        transcripts = self.get_transcripts()
        if self.visual_features:
            video = self.get_visual_features()
        else:
            video = self.get_videos()
        audio = self.get_audios()
        speaker = self.get_speakers()

        return (transcripts, video, audio, speaker)

    def get_data(self):
        """
        Method returns the data as a tuple of input and labels. Specifically:
        (inputs, labels)
        """
        return (self.get_inputs(), self.get_labels())


class Utterance(object):
    """
    Class for representing a single utterance in all 3 modalities
    """
    def __init__(self, dialogue_id, utterance_id, transcript, speaker, emotion, sentiment, file_path, utt_audio, name):
        self.dialogue_id = dialogue_id
        self.utterance_id = utterance_id
        self.transcript = transcript
        self.speaker = speaker
        self.emotion = emotion
        self.sentiment = sentiment
        self.file_path = file_path
        self.utt_audio = utt_audio
        self.name = name

    def get_transcript(self):
        """
        Returns a string representation of the transcript
        """
        # TODO: Still some issues with parsing the transcript, specifically wrt special symbols
        return self.transcript

    def get_speaker(self):
        """
        Returns the integer-mapped speaker id
        """
        return self.speaker

    def get_label(self):
        """
        Returns a tuple of the integer-mapped label as (emotion, sentiment)
        """
        #print(self.emotion)
        return (self.emotion, self.sentiment)

    def load_video(self):
        """
        Loads the video into memory and converts the frames into a pyTorch tensor
        """
        #print(self.file_path)
        return video_to_tensor(self.file_path)

    def get_cached_visual_features(self, max_persons=7, output_size=224, sampling_rate=30, display_images=False):

        cache_path = './cache'
        setting_path = os.path.join(cache_path, 'persons_{}_rate_{}_size_{}'.format(max_persons, sampling_rate, output_size))
        file_path = os.path.join(setting_path, self.name + '_dia_{}_utt_{}.pth'.format(self.dialogue_id, self.utterance_id))
        if not os.path.exists(cache_path):
            os.mkdir(cache_path)
        if not os.path.exists(setting_path):
            os.mkdir(setting_path)
        if not os.path.exists(file_path):
            #print("No cached features found, generating new features for dialogue: {}, utterance: {} ({}, {}, {})".format(self.dialogue_id, self.utterance_id, max_persons, sampling_rate, output_size))
            video_tensor = self.load_video()
            face_vector = detect_faces_mtcnn(video_tensor, max_persons, output_size, sampling_rate, display_images)
            #return face_vector
            torch.save(face_vector, file_path)
        #else:
            #print("Retrieved cached visual features for dialogue: {}, utterance: {} ({}, {}, {})".format(self.dialogue_id, self.utterance_id, max_persons, sampling_rate, output_size))
        #print(torch.load(file_path))
        return torch.load(file_path)

    def load_audio(self):
        """
        Returns the Audio embeddings.
        """
        return self.utt_audio


class MELDDataset(Dataset):
    """
    Class representing MELD dataset. Initialization is against a csv file and
    root directory.

    Accessing the dataset via an accessor will load the appropriate video and
    audio representations in memory and will be returned in the form:

        ([transcript], [video], [audio]) ([emotion_label], [sentiment_label])

    if visual_features = True,

        video is a list of face tensors detected by the mtcnn network
        otherwise, video is raw video tensors 
    
    Attributes:

    csv_recods: pandas representation of csv file
    root_dir: pah to root directory of data

    speaker_mapping: dictionary mapping speaker name to speaker id
    emotion_mapping: dictionary mapping emotion to emotion id
    sentiment_mapping: dictionary mapping sentiment to sentiment id

    Methods:

    load_sample_audio(index): loads the audio tuple retrieved from index
    load_sample_video(index): loads the video tensor retrieved from index
    """

    def __init__(self, csv_file, root_dir, audio_embs, name, visual_features=False):
        self.csv_records = pd.read_csv(csv_file)
        self.root_dir = os.path.abspath(root_dir)
        self.name = name

        speaker_set = set(self.csv_records.loc[:, "Speaker"].values.tolist())
        emotion_set = set(self.csv_records.loc[:, "Emotion"].values.tolist())
        sentiment_set = set(self.csv_records.loc[:, "Sentiment"].values.tolist())

        # TODO: we have combination mappings right now (eg. "Monica and Rachael")
        self.speaker_mapping = {speaker: id for id, speaker in enumerate(speaker_set)}
        #self.emotion_mapping = {emotion: id for id, emotion in enumerate(emotion_set)}

        # ported from the mapping used for frame attention network 
        self.emotion_mapping = {"joy": 0, "anger": 1, "disgust": 2, "fear": 3, "sadness": 4, "neutral": 5, "surprise": 6}
        print(self.emotion_mapping)
        self.sentiment_mapping = {sentiment: id for id, sentiment in enumerate(sentiment_set)}

#        print("Speaker mapping: {}".format(self.speaker_mapping))
#        print("Emotion mapping: {}".format(self.emotion_mapping))
#        print("Sentiment mapping: {}".format(self.sentiment_mapping))
        #print([self.speakers_to_label[key] for key in self.csv_records.loc[:, "Speaker"].values.tolist()])

#        print(self.csv_records.iloc[0:10,:])
        dialogues = {}
        for record in self.csv_records.loc[:].values:
#            print(record)
            id, transcript, speaker, emotion, sentiment, d_id, u_id, _, _, _, _ = record

            if d_id not in dialogues.keys():
                dialogues[d_id] = []
            # TODO: Still some issues with parsing the transcript, specifically wrt special symbols
            file_path = "dia{}_utt{}.mp4".format(d_id, u_id)
            file_path = os.path.join(self.root_dir, file_path)
            utt_audio_embed_id = str(d_id) + "_" + str(u_id)
            try:
                utt_audio_embed = audio_embs[utt_audio_embed_id]
            except KeyError:
                utt_audio_embed = (torch.zeros(128, 1).to('cuda'), torch.zeros(20, 1).to('cuda'))
            utterance = Utterance(
                d_id,
                u_id,
                transcript,
                self.speaker_mapping[speaker],
                self.emotion_mapping[emotion],
                self.sentiment_mapping[sentiment],
                file_path,
                utt_audio_embed,
                self.name
            )
            dialogues[d_id].append(utterance)

        self.data = []
        for d_id, utterances in dialogues.items():
            # Assumes no gaps in dialogue id
            # Assumes no gaps in utterance ids            
            utteraences = utterances.sort(key=lambda x: x.utterance_id)
            self.data.append(Dialogue(d_id, utterances, visual_features=visual_features))

    def __len__(self):
        return len(self.data)

    def __getitem__(self, idx):
        return self.data[idx].get_data()

    def load_sample_transcript(self, idx):
        return self.data[idx].get_transcript()

    def load_sample_audio(self, idx):
        return self.data[idx].load_audio()

    def load_sample_video(self, idx):
        return self.data[idx].load_video()

    """
    def load_sample_audio(audio_file_address):
        return torchaudio.load(audio_file_address)

    def load_transcript(csv_file_address):
        return None

    def load_image(image_file_address):
        image = Image.open(image_file_address)
        image = ToTensor()(image).unsqueeze(0) # unsqueeze to add artificial first dimension
        image = Variable(image)
        return image

    def load_image_set(image_dir_address):

        if not image_dir_address.endswith("/"):
            image_dir_address += "/"

        image_list = []
        for file_name in os.listdir(image_dir_address):
            image_tensor = load_image(image_dir_address + file_name)
            image_list.append(image_tensor)

        image_tensors = torch.cat(image_list, dim=0)
        return image_tensors
    """
