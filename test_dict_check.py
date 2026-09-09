import json
with open("dict_merged.json","r",encoding="utf-8") as f:
    d=json.load(f)
missing = []
common = ["the","is","open","me","you","can","are","my","to","it","and","in","on","not","this","that","with","for","at","do","have","has","had","a","an","if","or","be","so","no","yes","go","come","look","see","know","think","want","need","get","give","take","make","say","tell","ask","try","use","put","turn","close","run","stop","wait","back","here","there","very","much","just","about","also","then","now","when","what","where","how","who","which","why","all","some","any","each","every","many","few","more","most","other","such","only","own","same","than","too","very","well","still","already","again","always","never","sometimes","often","even","enough","quite","really","right","down","up","out","off","over","under","into","from","through","between","before","after","above","below","near","far","left","right","first","last","next","new","old","big","small","long","short","high","low","good","bad","hot","cold","dark","light","hard","soft","fast","slow","sure","true","wrong","true","false"]
for w in common:
    found = w in d
    if not found:
        missing.append(w)
    print(f"  {w}: {'YES' if found else 'NO'}")
print(f"\nMissing {len(missing)} words: {missing}")
