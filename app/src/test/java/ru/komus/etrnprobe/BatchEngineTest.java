package ru.komus.etrnprobe;
import org.junit.Test;
import static org.junit.Assert.*;
import java.util.*;
import java.io.IOException;
import static ru.komus.etrnprobe.BatchEngine.*;

/** Synthetic gateway scenarios. Signatures are dummy bytes; no cryptographic claim. */
public class BatchEngineTest {
    static class Memory implements Store {
        Snapshot data;boolean fail;
        public Snapshot load(){return data==null?null:data.copy();}
        public void save(Snapshot s)throws Exception{if(fail)throw new IOException("disk");data=s.copy();}
    }
    static List<Document> docs(int n){List<Document> d=new ArrayList<>();for(int i=0;i<n;i++)d.add(new Document("d"+i,"r"+i,"s"+i,"Принят","account/user"));return d;}
    static class Fake implements Gateway {
        int prepares,creates,polls,applies,reads;boolean approved=true,timeoutCreate,timeoutApply,readback=true,changed,emptyFiles,duplicateFiles,foreignFile;
        String current="account/user";int failOnApply=-1;Status status=Status.SIGNED;List<Signature> supplied;
        final Set<String> done=new HashSet<>();final List<FileRef> prepared=new ArrayList<>();
        public String principal(){return current;}
        public boolean hasVerifiedCrossDocumentContract(){return approved;}
        public List<FileRef> prepare(Document d){prepares++;if(emptyFiles)return Collections.emptyList();FileRef f=new FileRef(foreignFile?"other":d.id,duplicateFiles?"same":"f"+d.id,"hash-"+d.id);prepared.add(f);return Arrays.asList(f);}
        public String create(List<FileRef> f)throws Exception{creates++;if(timeoutCreate)throw new IOException("timeout");return "op-synthetic";}
        public Poll poll(String op){polls++;List<Signature> out=new ArrayList<>();for(FileRef f:prepared)out.add(sig(f.document,f.attachment,f.hash,"account/user"));return new Poll(status,supplied==null?out:supplied);}
        public boolean unchanged(Document d,List<FileRef> f){return !changed;}
        public void apply(Document d,List<Signature> s)throws Exception{applies++;for(Signature x:s)assertEquals(d.id,x.document);done.add(d.id);if(timeoutApply||applies==failOnApply)throw new IOException("apply timeout");}
        public boolean confirmed(Document d,List<FileRef> f){reads++;return readback&&done.contains(d.id);}
    }
    static Signature sig(String d,String f,String h,String p){return new Signature(d,f,h,p,new byte[]{1,2,3});}
    static BatchEngine engine(List<Document> d,Fake f,Memory m)throws Exception{return new BatchEngine(d,f,(file,s,p)->true,m);}
    static BatchEngine ready(Fake f,Memory m)throws Exception{BatchEngine e=engine(docs(2),f,m);e.prepare(true);return e;}
    static BatchEngine waiting(Fake f,Memory m)throws Exception{BatchEngine e=ready(f,m);e.submit(true);return e;}
    @Test public void fullTwoDocumentCycle()throws Exception{Fake f=new Fake();BatchEngine e=waiting(f,new Memory());e.continueAfterSigning();assertEquals(Phase.COMPLETE,e.snapshot().phase);assertEquals(2,f.applies);assertEquals(1,f.creates);assertEquals(2,e.snapshot().confirmed.size());}
    @Test public void fullFiftyDocumentCycle()throws Exception{Fake f=new Fake();BatchEngine e=engine(docs(50),f,new Memory());e.prepare(true);e.submit(true);e.continueAfterSigning();assertEquals(Phase.COMPLETE,e.snapshot().phase);assertEquals(1,f.creates);assertEquals(50,f.applies);}
    @Test public void operationIdIsNotCompletion()throws Exception{BatchEngine e=waiting(new Fake(),new Memory());assertEquals(Phase.WAITING,e.snapshot().phase);assertTrue(e.snapshot().confirmed.isEmpty());}
    @Test public void preparationNeedsConsent()throws Exception{Fake f=new Fake();BatchEngine e=engine(docs(2),f,new Memory());assertThrows(IllegalStateException.class,()->e.prepare(false));assertEquals(0,f.prepares);}
    @Test public void signingNeedsSeparateConsent()throws Exception{Fake f=new Fake();BatchEngine e=ready(f,new Memory());assertThrows(IllegalStateException.class,()->e.submit(false));assertEquals(0,f.creates);}
    @Test public void repeatedCreateIsBlocked()throws Exception{Fake f=new Fake();BatchEngine e=waiting(f,new Memory());assertThrows(IllegalStateException.class,()->e.submit(true));assertEquals(1,f.creates);}
    @Test public void repeatedPrepareIsBlocked()throws Exception{Fake f=new Fake();BatchEngine e=ready(f,new Memory());assertThrows(IllegalStateException.class,()->e.prepare(true));assertEquals(2,f.prepares);}
    @Test public void createTimeoutNeverTriggersRetry()throws Exception{Fake f=new Fake();f.timeoutCreate=true;Memory m=new Memory();BatchEngine e=ready(f,m);assertThrows(IOException.class,()->e.submit(true));BatchEngine resumed=engine(docs(2),f,m);assertThrows(IllegalStateException.class,()->resumed.submit(true));assertEquals(1,f.creates);assertEquals(Phase.UNKNOWN,resumed.snapshot().phase);}
    @Test public void restartWhileWaitingUsesSameOperation()throws Exception{Fake f=new Fake();Memory m=new Memory();waiting(f,m);BatchEngine e=engine(docs(2),f,m);e.continueAfterSigning();assertEquals(Phase.COMPLETE,e.snapshot().phase);assertEquals(1,f.creates);}
    @Test public void rejectedCrossDocumentContractDoesNotCallCreate()throws Exception{Fake f=new Fake();f.approved=false;BatchEngine e=ready(f,new Memory());e.submit(true);assertEquals(Phase.BLOCKED,e.snapshot().phase);assertEquals(0,f.creates);}
    @Test public void incompleteSignatureSetCannotFinalize()throws Exception{Fake f=new Fake();BatchEngine e=waiting(f,new Memory());f.supplied=Arrays.asList(sig("d0","fd0","hash-d0","account/user"));e.continueAfterSigning();assertEquals(Phase.BLOCKED,e.snapshot().phase);assertEquals(0,f.applies);}
    @Test public void reorderedSignaturesMatchByIdNotPosition()throws Exception{Fake f=new Fake();BatchEngine e=waiting(f,new Memory());f.supplied=Arrays.asList(sig("d1","fd1","hash-d1","account/user"),sig("d0","fd0","hash-d0","account/user"));e.continueAfterSigning();assertEquals(Phase.COMPLETE,e.snapshot().phase);}
    @Test public void duplicateSignatureIsRejected()throws Exception{Fake f=new Fake();BatchEngine e=waiting(f,new Memory());Signature s=sig("d0","fd0","hash-d0","account/user");f.supplied=Arrays.asList(s,s);e.continueAfterSigning();assertEquals(Phase.BLOCKED,e.snapshot().phase);assertEquals(0,f.applies);}
    @Test public void foreignSignatureIsRejected()throws Exception{Fake f=new Fake();BatchEngine e=waiting(f,new Memory());f.supplied=Arrays.asList(sig("other","fd0","hash-d0","account/user"),sig("d1","fd1","hash-d1","account/user"));e.continueAfterSigning();assertEquals(0,f.applies);assertEquals(Phase.BLOCKED,e.snapshot().phase);}
    @Test public void changedHashIsRejected()throws Exception{Fake f=new Fake();BatchEngine e=waiting(f,new Memory());f.supplied=Arrays.asList(sig("d0","fd0","changed","account/user"),sig("d1","fd1","hash-d1","account/user"));e.continueAfterSigning();assertEquals(0,f.applies);}
    @Test public void wrongSignerIsRejected()throws Exception{Fake f=new Fake();BatchEngine e=waiting(f,new Memory());f.supplied=Arrays.asList(sig("d0","fd0","hash-d0","other"),sig("d1","fd1","hash-d1","account/user"));e.continueAfterSigning();assertEquals(0,f.applies);}
    @Test public void verifierFailureBlocksAllDocuments()throws Exception{Fake f=new Fake();Memory m=new Memory();BatchEngine e=new BatchEngine(docs(2),f,(file,s,p)->false,m);e.prepare(true);e.submit(true);e.continueAfterSigning();assertEquals(Phase.BLOCKED,e.snapshot().phase);assertEquals(0,f.applies);}
    @Test public void refusalIsNotSuccess()throws Exception{Fake f=new Fake();BatchEngine e=waiting(f,new Memory());f.status=Status.REFUSED;e.continueAfterSigning();assertEquals(Phase.BLOCKED,e.snapshot().phase);assertEquals(0,f.applies);}
    @Test public void unknownSigningStateIsNotSuccess()throws Exception{Fake f=new Fake();BatchEngine e=waiting(f,new Memory());f.status=Status.UNKNOWN;e.continueAfterSigning();assertEquals(Phase.BLOCKED,e.snapshot().phase);}
    @Test public void pendingStatusDoesNotFinalize()throws Exception{Fake f=new Fake();BatchEngine e=waiting(f,new Memory());f.status=Status.WAITING;e.continueAfterSigning();assertEquals(Phase.WAITING,e.snapshot().phase);assertEquals(0,f.applies);}
    @Test public void applyTimeoutIsReconciledByReadOnly()throws Exception{Fake f=new Fake();f.timeoutApply=true;Memory m=new Memory();BatchEngine e=waiting(f,m);assertThrows(IOException.class,()->e.continueAfterSigning());BatchEngine resumed=engine(docs(2),f,m);f.timeoutApply=false;resumed.reconcile();resumed.continueAfterSigning();assertEquals(Phase.COMPLETE,resumed.snapshot().phase);assertEquals(2,f.applies);}
    @Test public void partialCompletionIsRetainedAcrossRestart()throws Exception{Fake f=new Fake();f.failOnApply=2;Memory m=new Memory();BatchEngine e=waiting(f,m);assertThrows(IOException.class,()->e.continueAfterSigning());assertEquals(1,e.snapshot().confirmed.size());BatchEngine resumed=engine(docs(2),f,m);resumed.reconcile();assertEquals(Phase.COMPLETE,resumed.snapshot().phase);assertEquals(2,f.applies);}
    @Test public void httpSuccessWithoutStageReadbackIsNotCompletion()throws Exception{Fake f=new Fake();f.readback=false;BatchEngine e=waiting(f,new Memory());e.continueAfterSigning();assertEquals(Phase.UNKNOWN,e.snapshot().phase);assertEquals(1,f.applies);}
    @Test public void failedReadbackDoesNotBlindlyReapply()throws Exception{Fake f=new Fake();f.readback=false;Memory m=new Memory();BatchEngine e=waiting(f,m);e.continueAfterSigning();BatchEngine r=engine(docs(2),f,m);r.reconcile();assertEquals(Phase.UNKNOWN,r.snapshot().phase);assertEquals(1,f.applies);}
    @Test public void accountSwitchBlocksSubsequentCalls()throws Exception{Fake f=new Fake();BatchEngine e=ready(f,new Memory());f.current="other";assertThrows(IllegalStateException.class,()->e.submit(true));assertEquals(0,f.creates);}
    @Test public void changedDocumentBeforeCreateIsBlocked()throws Exception{Fake f=new Fake();BatchEngine e=ready(f,new Memory());f.changed=true;e.submit(true);assertEquals(0,f.creates);}
    @Test public void changedDocumentAfterSigningIsBlocked()throws Exception{Fake f=new Fake();BatchEngine e=waiting(f,new Memory());f.changed=true;e.continueAfterSigning();assertEquals(0,f.applies);}
    @Test public void duplicateDocumentPlanIsBlocked()throws Exception{List<Document>d=docs(2);d.set(1,d.get(0));assertThrows(IllegalArgumentException.class,()->engine(d,new Fake(),new Memory()));}
    @Test public void changedRevisionCannotResumeOldJournal()throws Exception{Fake f=new Fake();Memory m=new Memory();ready(f,m);List<Document>d=docs(2);d.set(0,new Document("d0","new-revision","s0","Принят","account/user"));assertThrows(IllegalStateException.class,()->engine(d,f,m));}
    @Test public void mixedOwnersCannotBeBatched()throws Exception{List<Document>d=docs(2);d.set(1,new Document("d1","r1","s1","Принят","other"));assertThrows(IllegalArgumentException.class,()->engine(d,new Fake(),new Memory()));}
    @Test public void mixedActionsCannotBeBatched()throws Exception{List<Document>d=docs(2);d.set(1,new Document("d1","r1","s1","Выдан","account/user"));assertThrows(IllegalArgumentException.class,()->engine(d,new Fake(),new Memory()));}
    @Test public void storageFailurePreventsNetworkSideEffect()throws Exception{Fake f=new Fake();Memory m=new Memory();BatchEngine e=ready(f,m);m.fail=true;assertThrows(IOException.class,()->e.submit(true));assertEquals(0,f.creates);}
    @Test public void duplicateAttachmentIdStopsPreparation()throws Exception{Fake f=new Fake();f.duplicateFiles=true;BatchEngine e=ready(f,new Memory());assertEquals(Phase.BLOCKED,e.snapshot().phase);}
    @Test public void wrongPreparedDocumentStopsPreparation()throws Exception{Fake f=new Fake();f.foreignFile=true;BatchEngine e=ready(f,new Memory());assertEquals(Phase.BLOCKED,e.snapshot().phase);}
    @Test public void emptyPreparedFilesStopBeforeSigning()throws Exception{Fake f=new Fake();f.emptyFiles=true;BatchEngine e=ready(f,new Memory());assertEquals(Phase.BLOCKED,e.snapshot().phase);assertEquals(0,f.creates);}
    @Test public void externalSnapshotCannotAlterActiveState()throws Exception{BatchEngine e=waiting(new Fake(),new Memory());Snapshot x=e.snapshot();x.phase=Phase.COMPLETE;x.prepared.clear();assertEquals(Phase.WAITING,e.snapshot().phase);assertEquals(2,e.snapshot().prepared.size());}
}
