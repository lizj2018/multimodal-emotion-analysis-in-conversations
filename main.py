import torch
import torch.nn as nn
import torch.optim as optim
from dataset import MELDDataset, Utterance
import pickle
from dummy_model import DummyModel
from torch.utils.data import DataLoader
from models.config import Config
from models.dialogue_gcn import DialogueGCN

from torch.utils.data import ConcatDataset
from sklearn.metrics import f1_score, confusion_matrix
from models.expression_detector import ExpressionDetector, AttentionConvWrapper



config = Config()
if config.use_our_audio:
    audio_embed_path_train = "../MELD.Raw/train_audio.pkl"
    audio_embed_path_val = "../MELD.Raw/dev_audio.pkl"
    audio_embed_path_test = "../MELD.Raw/test_audio.pkl"
    train_audio_emb = pickle.load(open(audio_embed_path_train, 'rb'))
    val_audio_emb = pickle.load(open(audio_embed_path_val, 'rb'))
    test_audio_emb = pickle.load(open(audio_embed_path_test, 'rb'))
else:    
    #audio_embed_path = "../MELD.Features.Models/features/audio_embeddings_feature_selection_emotion.pkl"
    audio_embed_path = "../MELD.Raw/audio_embeddings_feature_selection_sentiment.pkl"
    train_audio_emb, val_audio_emb, test_audio_emb = pickle.load(open(audio_embed_path, 'rb'))

val_dataset = MELDDataset("../MELD.Raw/dev_sent_emo.csv", "../MELD.Raw/dev_splits_complete/", val_audio_emb, name="val", visual_features=False)
train_dataset = MELDDataset("../MELD.Raw/train_sent_emo.csv", "../MELD.Raw/train_splits/", train_audio_emb, name="train", visual_features=False)

if config.eval_on_test:
    train_dataset = ConcatDataset([train_dataset, val_dataset])
test_dataset = MELDDataset("../MELD.Raw/test_sent_emo.csv", "../MELD.Raw/output_repeated_splits_test", test_audio_emb, name="test", visual_features=False)

def train_and_validate(model_name, model, optimiser, loss_emotion, loss_sentiment, train_data_loader, val_data_loader):
    # dummpy value of 0as a lower bound for the accuracy
    best_emotion_accuracy_so_far = 0
    num_of_no_improvements = 0
    for epoch in range(config.num_epochs):
        model = model.train()
        loss_acc = 0
        total_epoch_loss = 0
        for i, (batch_input, batch_labels) in enumerate(train_data_loader):
            batch_loss = train_step(model, batch_input, batch_labels, loss_emotion, loss_sentiment, optimiser)
            loss_acc += batch_loss
            total_epoch_loss += batch_loss
            if (i % 100 == 0):
                print("Epoch[" + str(epoch) + "/" + str(config.num_epochs) +"] - batch " + str(i) + " Error: " + str(loss_acc))
                #loss_acc = 0

        model = model.eval()
        emotion_predicted_labels = []
        sentiment_predicted_labels = []
        emotion_target_labels = []
        sentiment_target_labels = []
        for i, (val_batch_input, val_batch_labels) in enumerate(val_data_loader):
            batch_emotion_correct_predicted_labels, batch_sentiment_predicted_labels, batch_val_count = validate_step(model, val_batch_input, val_batch_labels)
            emotion_predicted_labels.append(batch_emotion_correct_predicted_labels)
            sentiment_predicted_labels.append(batch_sentiment_predicted_labels)
            emotion_target_labels.append(torch.cat(val_batch_labels[0],0))
            sentiment_target_labels.append(torch.cat(val_batch_labels[1],0))

        emotion_predicted_labels = torch.cat(emotion_predicted_labels, 0).cuda()
        sentiment_predicted_labels = torch.cat(sentiment_predicted_labels, 0).cuda()
        emotion_target_labels = torch.cat(emotion_target_labels, 0)
        sentiment_target_labels = torch.cat(sentiment_target_labels, 0)
        target_labels = torch.cat([emotion_target_labels.unsqueeze(1), sentiment_target_labels.unsqueeze(1)], 1).cuda()

        emotion_f1_score = f1_score(emotion_target_labels.cpu().numpy(), emotion_predicted_labels.cpu().numpy(), average='weighted')        

        emotion_accuracy, sentiment_accuracy = get_accuracy(emotion_predicted_labels, sentiment_predicted_labels, target_labels)
        #emotion_recalls, sentiment_recalls = get_recall_for_each_class(emotion_predicted_labels, sentiment_predicted_labels, target_labels)
        #emotion_precisions, sentiment_precisions = get_precision_for_each_class(emotion_predicted_labels, sentiment_predicted_labels, target_labels)
        #emotion_f1s, sentiment_f1s = get_f1_score_for_each_class(emotion_precisions, emotion_recalls, sentiment_precisions, sentiment_recalls)
        #emotion_weighted_f1, sentiment_weighted_f1 = get_weighted_F1(emotion_f1s, sentiment_f1s, target_labels)
        confusion = confusion_matrix(emotion_target_labels.cpu().numpy(), emotion_predicted_labels.cpu().numpy())
        print("Validation Accuracy (Emotion): ", emotion_accuracy)
        print("F1 Weighted", emotion_f1_score)
        print("Confusion matrix", confusion)
        #if ((emotion_correct_count / val_count) > best_emotion_accuracy_so_far):
        #    best_emotion_accuracy_so_far = (emotion_correct_count / val_count)
        torch.save({
            'epoch': epoch,
            'model_state_dict': model.state_dict(),
            'optimizer_state_dict': optimiser.state_dict(),
            'loss': total_epoch_loss
        },  "model_saves/" + model_name + "_epoch_image_only_" + str(epoch) +".pt")
        #    num_of_no_improvements = 0
        #    print("BEST VALIDATION UPDATED!")
        #else:
        #    num_of_no_improvements += 1
        #    if (num_of_no_improvements > 3):
        #        break

def get_weighted_F1(emotion_f1s, sentiment_f1s, targets):
    emotion_weighted_f1 = 0
    for emotion in emotion_f1s.keys():
        emotion_indices_count = (targets[:,0]==emotion).sum()
        emotion_weighted_f1 += emotion_f1s[emotion] * emotion_indices_count
    emotion_weighted_f1 = emotion_weighted_f1 / targets.size(0)

    sentiment_weighted_f1 = 0
    for sentiment in sentiment_f1s.keys():
        sentiment_indices_count = (targets[:,0]==sentiment).sum()
        sentiment_weighted_f1 += sentiment_f1s[sentiment] * sentiment_indices_count
    sentiment_weighted_f1 = sentiment_weighted_f1 / targets.size(0)

    return emotion_weighted_f1, sentiment_weighted_f1

def get_accuracy(predicted_emotion, predicted_sentiment, target):
    print(target.size())
    print(predicted_emotion.size())
    print(predicted_sentiment.size())
    emotion_accuracy_acc = torch.eq(predicted_emotion, target[:,0]).sum().item() / target.size(0)
    sentiment_accuracy_acc = torch.eq(predicted_sentiment, target[:,1]).sum().item() / target.size(0)
    return emotion_accuracy_acc, sentiment_accuracy_acc

def get_f1_score_for_each_class(emotion_precisions, emotion_recalls, sentiment_precisions, sentiment_recalls):
    emotion_f1s = {}
    for emotion in emotion_precisions.keys():
        emotion_f1s[emotion] = 2 * (emotion_precisions[emotion] * emotion_recalls[emotion]) / (emotion_precisions[emotion] + emotion_recalls[emotion] + 10E-7)

    sentiment_f1s = {}
    for sentiment in sentiment_precisions.keys():
        sentiment_f1s[sentiment] = 2 * (sentiment_precisions[sentiment] * sentiment_recalls[sentiment]) / (sentiment_precisions[sentiment] + sentiment_recalls[sentiment] + 10E-7)

    return emotion_f1s, sentiment_f1s

def get_precision_for_each_class(predicted_emotion, predicted_sentiment, target):
    emotion_precisions = {}
    for emotion in torch.unique(target[:,0]):
        emotion_indices = (predicted_emotion==emotion)
        emotion_correctly_classified = torch.eq(target[emotion_indices.nonzero(),0], predicted_emotion[emotion_indices.nonzero()]).sum()
        emotion_total_assigned = emotion_indices.sum()
        emotion_precisions[emotion.item()] = emotion_correctly_classified.item() / (emotion_total_assigned.item() + 10E-7)

    sentiment_precisions = {}
    for sentiment in torch.unique(target[:,1]):
        sentiment_indices = (predicted_sentiment==sentiment)
        sentiment_correctly_classified = torch.eq(target[sentiment_indices.nonzero(),1], predicted_emotion[sentiment_indices.nonzero()]).sum()
        sentiment_total_assigned = sentiment_indices.sum()
        sentiment_precisions[sentiment.item()] = sentiment_correctly_classified.item() / (sentiment_total_assigned.item() + 10E-7)

    return emotion_precisions, sentiment_precisions

def get_recall_for_each_class(predicted_emotion, predicted_sentiment, target):
    emotion_recalls = {}
    for emotion in torch.unique(target[:,0]):
        emotion_indices = (target[:,0]==emotion)
        emotion_correctly_recalled = torch.eq(target[emotion_indices.nonzero(),0], predicted_emotion[emotion_indices.nonzero()]).sum()
        emotion_total_to_be_recalled = emotion_indices.sum()
        emotion_recalls[emotion.item()] = emotion_correctly_recalled.item() / emotion_total_to_be_recalled.item()

    sentiment_recalls = {}
    for sentiment in torch.unique(target[:,1]):
        sentiment_indices = (target[:,1]==sentiment)
        sentiment_correctly_recalled = torch.eq(target[sentiment_indices.nonzero(),1], predicted_emotion[sentiment_indices.nonzero()]).sum()
        sentiment_total_to_be_recalled = sentiment_indices.sum()
        sentiment_recalls[sentiment.item()] = sentiment_correctly_recalled.item() / sentiment_total_to_be_recalled.item()

    return emotion_recalls, sentiment_recalls

def test_model(model_name, model, test_loader):
    print("Testing " + model_name)
    model = model.eval()
    emotion_predicted_labels = []
    sentiment_predicted_labels = []
    emotion_target_labels = []
    sentiment_target_labels = []
    for i, (test_batch_input, test_batch_labels) in enumerate(test_loader):
        batch_emotion_correct_predicted_labels, batch_sentiment_predicted_labels, batch_val_count = validate_step(model, test_batch_input, test_batch_labels)
        emotion_predicted_labels.append(batch_emotion_correct_predicted_labels)
        sentiment_predicted_labels.append(batch_sentiment_predicted_labels)
        emotion_target_labels.append(torch.cat(test_batch_labels[0],0))
        sentiment_target_labels.append(torch.cat(test_batch_labels[1],0))

    emotion_predicted_labels = torch.cat(emotion_predicted_labels, 0).cuda()
    sentiment_predicted_labels = torch.cat(sentiment_predicted_labels, 0).cuda()
    emotion_target_labels = torch.cat(emotion_target_labels, 0)
    sentiment_target_labels = torch.cat(sentiment_target_labels, 0)
    target_labels = torch.cat([emotion_target_labels.unsqueeze(1), sentiment_target_labels.unsqueeze(1)], 1).cuda()

    emotion_f1_score = f1_score(emotion_target_labels.cpu().numpy(), emotion_predicted_labels.cpu().numpy(), average='weighted')        
    confusion = confusion_matrix(emotion_target_labels.cpu().numpy(), emotion_predicted_labels.cpu().numpy())
    emotion_accuracy, sentiment_accuracy = get_accuracy(emotion_predicted_labels, sentiment_predicted_labels, target_labels)
    #emotion_recalls, sentiment_recalls = get_recall_for_each_class(emotion_predicted_labels, sentiment_predicted_labels, target_labels)
    #emotion_precisions, sentiment_precisions = get_precision_for_each_class(emotion_predicted_labels, sentiment_predicted_labels, target_labels)
    #emotion_f1s, sentiment_f1s = get_f1_score_for_each_class(emotion_precisions, emotion_recalls, sentiment_precisions, sentiment_recalls)
    #emotion_weighted_f1, sentiment_weighted_f1 = get_weighted_F1(emotion_f1s, sentiment_f1s, target_labels)

    print("Validation Accuracy (Emotion): ", emotion_accuracy)
    print("F1 Weighted", emotion_f1_score)
    print("Confusion matrix", confusion)
    
def train_step(model, input, target, loss_emotion, loss_sentiment, optimiser):
    """Trains model for one batch of data."""
    optimiser.zero_grad()
    (batch_output_emotion, batch_output_sentiment) = model(input)
    target = torch.LongTensor(target).to("cuda")
    batch_loss_emotion = loss_emotion(batch_output_emotion, target[0])
    #batch_loss_sentiment = loss_sentiment(batch_output_sentiment, target[1])
    total_loss = batch_loss_emotion# + batch_loss_sentiment
    total_loss.backward()
    torch.nn.utils.clip_grad_norm_(model.parameters(), 1)
    optimiser.step()
    return total_loss.item()

def validate_step(model, input, target):
    target = torch.LongTensor(target).to("cuda")
    (output_logits_emotion, output_logits_sentiment) = model(input)
    output_labels_emotion = torch.argmax(output_logits_emotion, dim=1)
    output_labels_sentiment = torch.argmax(output_logits_sentiment, dim=1)
    #emotion_accuracy_acc = torch.eq(output_labels_emotion, target[0]).sum()
    #sentiment_accuracy_acc = torch.eq(output_labels_emotion, target[1]).sum()
    #return emotion_accuracy_acc, sentiment_accuracy_acc, target[0].size(0)
    return output_labels_emotion, output_labels_sentiment, target[0].size()

def test_step(model, input, target):
    target = torch.LongTensor(target).to("cuda")
    (output_logits_emotion, output_logits_sentiment) = model(input)
    output_labels_emotion = torch.argmax(output_logits_emotion, dim=1)
    output_labels_sentiment = torch.argmax(output_logits_sentiment, dim=1)
    #emotion_accuracy_acc = torch.eq(output_labels_emotion, target[0]).sum()
    #sentiment_accuracy_acc = torch.eq(output_labels_emotion, target[1]).sum()
    #return emotion_accuracy_acc, sentiment_accuracy_acc, target[0].size(0)
    return output_labels_emotion, output_labels_sentiment, target[0].size()

dumb_model = DummyModel()
emotion_criterion = nn.CrossEntropyLoss()
sentiment_criterion = nn.CrossEntropyLoss()
model_name = "Visual"
#train_loader = DataLoader(train_dataset, batch_size=100, shuffle=True)
#val_loader = DataLoader(val_dataset, batch_size=100, shuffle=True)
#test_loader = DataLoader(test_dataset, batch_size=100, shuffle=True)

#train_and_validate(model_name, dumb_model, optimisation_unit, emotion_criterion, sentiment_criterion, train_loader, val_loader)
#test_model(model_name, dumb_model, test_loader)
train_loader = DataLoader(train_dataset, batch_size=1, shuffle=True)
val_loader = DataLoader(val_dataset, batch_size=1, shuffle=True)
test_loader = DataLoader(test_dataset, batch_size=1, shuffle=True)

if config.model_type == 'dialoguegcn':
    model = DialogueGCN(config)
    model = model.to("cuda")
elif config.model_type == 'fan':
    model = ExpressionDetector(config.fan_weights_path, config.face_matching)
    model = model.to("cuda")
elif config.model_type == 'acn':
    model = AttentionConvWrapper()
    model = model.to("cuda")
if config.model_type == 'dummy':
    model = DummyModel()

#model.load_state_dict(torch.load('model_saves/DialogueGCN_epoch12.pt')['model_state_dict'])
#model.eval()
#test_model('gcn_13', model, test_loader)
#return
optimisation_unit = optim.Adam(model.parameters(), lr=config.lr, weight_decay=config.l2)

for i in range(20):
    train_and_validate(model_name + str(i), model, optimisation_unit, emotion_criterion, sentiment_criterion, train_loader, val_loader)
    test_model(model_name + str(i), model, test_loader)
