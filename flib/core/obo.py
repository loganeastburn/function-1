import sys
import logging
logging.basicConfig()
logger = logging.getLogger(__name__)
logger.setLevel(logging.ERROR)

import re
from collections import defaultdict
from idmap import IDMap
from gmt import GMT
import urllib2


class OBO:
    heads = None
    go_terms = None
    alt_id2std_id = None
    populated = None
    s_orgs = None

    # populate this field if you want to mark this GO as organism specific
    go_organism_tax_id = None

    """
    Pass the obo file
    """

    def __init__(self, obo_file=None):
        self.heads = []
        self.go_terms = {}
        self.go_obsolete = {}
        self.alt_id2std_id = {}
        self.name2synonyms = {}
        self.populated = False
        self.s_orgs = []
        self.__meta = {}

        if obo_file:
            self.load_obo(obo_file)

    def load_obo(self, obo_file, remote_location=False, timeout=5):

        if remote_location:
            obo = urllib2.urlopen(obo_file, timeout=timeout)
            lines = obo.readlines()
        else:
            obo = open(obo_file, 'r')
            lines = obo.readlines()

        inside = False
        gterm = None
        for line in lines:
            fields = line.rstrip().split()

            if len(fields) < 1:
                continue

            elif not inside and not len(self.go_terms.keys()) and len(fields) > 1:
                key = fields[0]
                if key.endswith(':'):
                    key = key[:-1]
                self.__meta[key] = fields[1]
            elif fields[0] == '[Term]':
                if gterm:
                    if gterm.head:
                        self.heads.append(gterm)
                inside = True
            elif fields[0] == '[Typedef]':
                if gterm:
                    if gterm.head:
                        self.heads.append(gterm)
                inside = False

            elif inside and fields[0] == 'id:':
                # print fields[1]
                if fields[1] in self.go_terms:
                    gterm = self.go_terms[fields[1]]
                else:
                    gterm = GOTerm(fields[1])
                    self.go_terms[gterm.get_id()] = gterm
                # print self.go_terms[fields[1]]
            elif inside and fields[0] == 'name:':
                fields.pop(0)
                gterm.fullname = ' '.join(fields)
                name = '_'.join(fields)
                name = name.replace('\'', '')
                name = re.sub('[^\w\s_-]', '_', name).strip().lower()
                name = re.sub('[-\s_]+', '_', name)
                gterm.name = name
            elif inside and fields[0] == 'namespace:':
                gterm.namespace = fields[1]
            elif inside and fields[0] == 'def:':
                gterm.desc = ' '.join(fields[1:]).split('\"')[1]
            elif inside and fields[0] == 'alt_id:':
                gterm.alt_id.append(fields[1])
                self.alt_id2std_id[fields[1]] = gterm.get_id()
            elif inside and fields[0] == 'is_a:':
                gterm.head = False
                fields.pop(0)
                pgo_id = fields.pop(0)
                if pgo_id not in self.go_terms:
                    self.go_terms[pgo_id] = GOTerm(pgo_id)

                gterm.is_a.append(self.go_terms[pgo_id])
                self.go_terms[pgo_id].parent_of.add(gterm)
                gterm.child_of.add(self.go_terms[pgo_id])
            elif inside and fields[0] == 'relationship:':
                if fields[1].find('has_part') != -1:
                    # has part is not a parental relationship -- it is actually
                    # for children.
                    continue
                gterm.head = False
                pgo_id = fields[2]
                if pgo_id not in self.go_terms:
                    self.go_terms[pgo_id] = GOTerm(pgo_id)
                # Check which relationship you are with this parent go term
                if fields[1] == 'regulates' or fields[
                        1] == 'positively_regulates' or fields[1] == 'negatively_regulates':
                    gterm.relationship_regulates.append(self.go_terms[pgo_id])
                elif fields[1] == 'part_of':
                    gterm.relationship_part_of.append(self.go_terms[pgo_id])
                else:
                    logger.info(
                        "Unknown relationship %s",
                        self.go_terms[pgo_id].name)
                    continue
                self.go_terms[pgo_id].parent_of.add(gterm)
                gterm.child_of.add(self.go_terms[pgo_id])
            elif inside and fields[0] == 'is_obsolete:':
                gterm.head = False
                del self.go_terms[gterm.get_id()]
                gterm.obsolete = True
                self.go_obsolete[gterm.get_id()] = gterm
            elif inside and fields[0] == 'synonym:':
                syn = ' '.join(fields[1:]).split('\"')[1]
                syn = syn.replace('lineage name: ', '')
                gterm.synonyms.append(syn)
                if gterm.name in self.name2synonyms:
                    self.name2synonyms[gterm.name].append(syn)
                else:
                    self.name2synonyms[gterm.name] = [syn]
            elif inside and fields[0] == 'xref:':
                tok = fields[1].split(':')
                if len(tok) > 1:
                    (xrefdb, xrefid) = fields[1].split(':')[0:2]
                    gterm.xrefs.setdefault(xrefdb, set()).add(xrefid)

        return True

    def propagate(self):
        """Propagate all gene annotations"""
        logger.info("Propagate gene annotations")
        for head_gterm in self.heads:
            logger.info("Propagating %s", head_gterm.name)
            self.propagate_recurse(head_gterm)

    def propagate_recurse(self, gterm):
        if not len(gterm.parent_of):
            logger.debug("Base case with term %s", gterm.name)
            return

        for child_term in gterm.parent_of:
            self.propagate_recurse(child_term)
            new_annotations = set()

            regulates_relation = (gterm in child_term.relationship_regulates)
            part_of_relation = (gterm in child_term.relationship_part_of)

            for annotation in child_term.annotations:
                copied_annotation = None
                # if this relation with child is a regulates(and its sub class)
                # filter annotations
                if regulates_relation:
                    # only add annotations that didn't come from a part of or
                    # regulates relationship
                    if annotation.ready_regulates_cutoff:
                        continue
                    else:
                        copied_annotation = annotation.prop_copy(
                            ready_regulates_cutoff=True)
                elif part_of_relation:
                    copied_annotation = annotation.prop_copy(
                        ready_regulates_cutoff=True)
                else:
                    copied_annotation = annotation.prop_copy()

                new_annotations.add(copied_annotation)
            gterm.annotations = gterm.annotations | new_annotations

    def get_term(self, tid):
        """Return GOTerm object corresponding with id=tid"""
        logger.debug('get_term: %s', tid)
        term = None
        try:
            term = self.go_terms[tid]
        except KeyError:
            try:
                term = self.go_terms[self.alt_id2std_id[tid]]
            except KeyError:
                logger.error('Term name does not exist: %s', tid)
        return term

    def get_meta_data(self, key):
        """Return metadata in obo corresponding to key"""
        if key in self.__meta:
            return self.__meta[key]
        else:
            return None

    def get_termobject_list(self, terms=None, p_namespace=None):
        """Return list of all GOTerms"""
        logger.info('get_termobject_list')
        if terms is None:
            terms = self.go_terms.keys()
        reterms = []
        for tid in terms:
            obo_term = self.get_term(tid)
            if obo_term is None:
                continue
            if p_namespace is not None and obo_term.namespace != p_namespace:
                continue
            reterms.append(obo_term)
        return reterms

    def get_obsolete_terms(self, p_namespace=None):
        """Return list of all obsolete GOTerms"""
        logger.info('get_obsolete_list')
        return self.go_obsolete.values()

    def get_termdict_list(self, terms=None, p_namespace=None):
        logger.info('get_termdict_list')
        tlist = self.get_termobject_list(terms=terms, p_namespace=p_namespace)
        reterms = []
        for obo_term in tlist:
            reterms.append({'oboid': obo_term.go_id, 'name': obo_term.name})
        return reterms

    def get_xref_mapping(self, prefix):
        """Return dict of terms mappings to external database ids"""
        xrefs = defaultdict(set)
        for term in self.get_termobject_list():
            ids = term.get_xrefs(prefix)
            if ids:
                for xref in ids:
                    xrefs[xref].add(term.go_id)
        return xrefs

    def as_gmt(self):
        """Return gene annotations as GMT object"""
        gmt = GMT()
        tlist = sorted(self.get_termobject_list())
        for term in tlist:
            if len(term.annotations):
                gmt.add_geneset(gsid=term.go_id, name=term.name)
            for annotation in term.annotations:
                gmt.add_gene(term.go_id, annotation.gid)
        return gmt

    def map_genes(self, id_name):
        """Map gene names using the idmap object id_name"""
        for go_term in self.go_terms.itervalues():
            go_term.map_genes(id_name)

    def populate_annotations(self, annotation_file, xdb_col=0,
                             gene_col=None, term_col=None, ref_col=5, ev_col=6, date_col=13):
        logger.info('Populate gene annotations: %s', annotation_file)
        details_col = 3
        f = open(annotation_file, 'r')
        for line in f:
            if line[0] == '!':
                continue
            fields = line.rstrip('\n').split('\t')

            xdb = fields[xdb_col]
            gene = fields[gene_col]
            go_id = fields[term_col]

            try:
                ref = fields[ref_col]
            except IndexError:
                ref = None
            try:
                ev = fields[ev_col]
            except IndexError:
                ev = None
            try:
                date = fields[date_col]
            except IndexError:
                date = None

            if date_col < len(fields):
                date = fields[date_col]
            else:
                date = None

            try:
                details = fields[details_col]
                if details == 'NOT':
                    continue
            except IndexError:
                pass
            go_term = self.get_term(go_id)
            if go_term is None:
                continue
            logger.info('Gene %s and term %s', gene, go_term.go_id)
            annotation = Annotation(
                xdb=xdb,
                gid=gene,
                ref=ref,
                evidence=ev,
                date=date,
                direct=True)
            go_term.annotations.add(annotation)

        f.close()
        self.populated = True

    def populate_annotations_from_gmt(self, gmt):
        for (gsid, genes) in gmt.genesets.iteritems():
            term = self.get_term(gsid)
            if term:
                for gid in genes:
                    term.add_annotation(gid)

    def add_annotation(self, go_id, gid, ref, direct):
        go_term = self.get_term(go_id)
        if not go_term:
            return False
        annot = Annotation(xdb=None, gid=gid, direct=direct, ref=ref)
        go_term.annotations.add(annot)
        return True

    def get_descendents(self, gterm):
        """Return propagated descendents of term"""
        if gterm not in self.go_terms:
            return set()
        term = self.go_terms[gterm]

        if len(term.parent_of) == 0:
            return set()

        child_terms = set()
        for child_term in term.parent_of:
            if child_term.namespace != term.namespace:
                continue
            child_terms.add(child_term.go_id)
            child_terms = child_terms | self.get_descendents(child_term.go_id)

        return child_terms

    def get_ancestors(self, gterm):
        """Return propagated ancestors of term"""
        if (gterm in self.go_terms) is False:
            return set()
        term = self.go_terms[gterm]

        if len(term.child_of) == 0:
            return set()

        parent_terms = set()
        for parent_term in term.child_of:
            if parent_term.namespace != term.namespace:
                logger.info("Parent and child terms are different namespaces: %s and %s",
                        parent_term, term)
                continue
            parent_terms.add(parent_term.go_id)
            parent_terms = parent_terms | self.get_ancestors(parent_term.go_id)

        return parent_terms

    def get_leaves(self, namespace='biological_process', min_annot=10):
        """Return a set of leaf terms from the ontology"""
        leaves, bottom = set(), set()
        for term in self.go_terms.values():
            if len(term.parent_of) == 0 and term.namespace == namespace and len(
                    term.annotations) >= min_annot:
                leaves.add(term)
        return leaves

    def print_to_dir(self, out_dir, terms=None, p_namespace=None):
        """Writes to out_dir each term and its gene annotations in individual files"""
        logger.info('print_terms')
        tlist = self.get_termobject_list(terms=terms, p_namespace=p_namespace)
        # print terms
        for term in tlist:
            id_set = set(term.get_annotated_genes())
            if len(id_set) == 0:
                continue
            output_fh = open(out_dir + '/' + term.name, 'w')
            # keep previous behavior w/ newline at end
            output_fh.write('\n'.join(id_set) + '\n')
            output_fh.close()

    def print_to_single_file(self, out_file, terms=None,
                             p_namespace=None, gene_asso_format=False):
        logger.info('print_to_single_file')
        tlist = sorted(
            self.get_termobject_list(
                terms=terms,
                p_namespace=p_namespace))
        f = open(out_file, 'w')
        for term in tlist:
            for annotation in term.annotations:
                if gene_asso_format:
                    to_print = [annotation.xdb if annotation.xdb else '',
                                annotation.gid if annotation.gid else '',
                                '', '',  # Gene Symbol, NOT/''
                                term.go_id if term.go_id else '',
                                annotation.ref if annotation.ref else '',
                                annotation.evidence if annotation.evidence else '',
                                annotation.date if annotation.date else '',
                                str(annotation.direct),
                                # Direct is added in to indicate prop status
                                # cross annotated is added in to indicate cross
                                # status
                                str(annotation.cross_annotated),
                                # if cross annotated, where the annotation is
                                # from
                                annotation.origin if annotation.cross_annotated else '',
                                # if cross annotated, then the evidence of the
                                # cross_annotation (e.g. bootstrap value,
                                # p-value)
                                str(annotation.ortho_evidence) if annotation.ortho_evidence else '', '', '']
                    print >> f, '\t'.join([str(x) for x in to_print])
                else:
                    print >> f, term.go_id + '\t' + term.name + '\t' + annotation.gid
        f.close()

    def print_to_gmt_file(self, out_file, terms=None, p_namespace=None):
        logger.info('print_to_gmt_file')
        tlist = sorted(
            self.get_termobject_list(
                terms=terms,
                p_namespace=p_namespace))
        f = open(out_file, 'w')
        for term in tlist:
            genes = set()
            for annotation in term.annotations:
                genes.add(annotation.gid)
            if len(genes) > 0:
                print >> f, term.go_id + '\t' + term.name + \
                    '\t' + '\t'.join(genes)
        f.close()

    def print_to_mat_file(self, out_file, terms=None, p_namespace=None):
        logger.info('print_to_mat_file')
        tlist = sorted(
            self.get_termobject_list(
                terms=terms,
                p_namespace=p_namespace))
        f = open(out_file, 'w')

        allgenes = set()
        genedict = defaultdict(set)
        termlist = []
        for term in tlist:
            if len(term.annotations) == 0:
                continue

            termlist.append(term.go_id)

            for annotation in term.annotations:
                allgenes.add(annotation.gid)
                genedict[annotation.gid].add(term.go_id)

        print >> f, '\t' + '\t'.join(termlist)
        for g in list(allgenes):
            row = []
            row.append(g)
            for termid in termlist:
                row.append('1' if termid in genedict[g] else '0')
            print >> f, '\t'.join(row)
        f.close()


class Annotation(object):

    def __init__(self, xdb=None, gid=None, ref=None, evidence=None, date=None, direct=False,
                 cross_annotated=False, origin=None, ortho_evidence=None, ready_regulates_cutoff=False):
        super(Annotation, self).__setattr__('xdb', xdb)
        super(Annotation, self).__setattr__('gid', gid)
        super(Annotation, self).__setattr__('ref', ref)
        super(Annotation, self).__setattr__('evidence', evidence)
        super(Annotation, self).__setattr__('date', date)
        super(Annotation, self).__setattr__('direct', direct)
        super(Annotation, self).__setattr__('cross_annotated', cross_annotated)
        super(Annotation, self).__setattr__('origin', origin)
        super(Annotation, self).__setattr__('ortho_evidence', ortho_evidence)
        super(
            Annotation,
            self).__setattr__(
            'ready_regulates_cutoff',
            ready_regulates_cutoff)

    def prop_copy(self, ready_regulates_cutoff=None):
        if ready_regulates_cutoff is None:
            ready_regulates_cutoff = self.ready_regulates_cutoff

        return Annotation(xdb=self.xdb, gid=self.gid, ref=self.ref,
                          evidence=self.evidence, date=self.date, direct=False, cross_annotated=False,
                          ortho_evidence=self.ortho_evidence, ready_regulates_cutoff=ready_regulates_cutoff)

    def __hash__(self):
        return hash((self.xdb, self.gid, self.ref, self.evidence, self.date,
                     self.direct, self.cross_annotated, self.ortho_evidence,
                     self.ready_regulates_cutoff, self.origin))

    def __eq__(self, other):
        return (self.xdb, self.gid, self.ref, self.evidence, self.date,
                self.direct, self.cross_annotated, self.ortho_evidence,
                self.ready_regulates_cutoff, self.origin).__eq__((other.xdb,
                                                                  other.gid, other.ref, other.evidence, other.date,
                                                                  other.direct, other.cross_annotated, other.ortho_evidence,
                                                                  other.ready_regulates_cutoff, other.origin))

    def __setattr__(self, *args):
        raise TypeError("Attempt to modify immutable object.")
    __delattr__ = __setattr__


class GOTerm:
    go_id = ''
    is_a = None
    relationship = None
    parent_of = None
    child_of = None
    annotations = None
    alt_id = None
    namespace = ''
    included_in_all = None
    valid_go_term = None
    cross_annotated_genes = None
    head = None
    name = None
    base_counts = None
    counts = None
    summary = None
    desc = None
    votes = None
    synonyms = None
    fullname = None
    xrefs = None
    obsolete = None

    def __init__(self, go_id):
        self.head = True
        self.go_id = go_id
        self.annotations = set([])
        self.cross_annotated_genes = set([])
        self.is_a = []
        self.relationship_regulates = []
        self.relationship_part_of = []
        self.parent_of = set()
        self.child_of = set()
        self.alt_id = []
        self.included_in_all = True
        self.valid_go_term = True
        self.name = None
        self.base_counts = None
        self.counts = None
        self.desc = None
        self.votes = set([])
        self.synonyms = []
        self.fullname = None
        self.xrefs = {}
        self.obsolete = False

    def __cmp__(self, other):
        return cmp(self.go_id, other.go_id)

    def __hash__(self):
        return(self.go_id.__hash__())

    def __repr__(self):
        return(self.go_id + ': ' + self.name)

    def get_id(self):
        return self.go_id

    def map_genes(self, id_name):
        """Map gene ids"""
        mapped_annotations_set = set([])
        for annotation in self.annotations:
            mapped_genes = id_name.get(annotation.gid)
            if mapped_genes is None and 'CELE_' in annotation.gid:
                mapped_genes = id_name.get(
                    annotation.gid[5:len(annotation.gid)])

            if mapped_genes is None:
                logger.warning('No matching gene id: %s', annotation.gid)
                continue
            for mgene in mapped_genes:
                mapped_annotations_set.add(Annotation(xdb=None, gid=mgene,
                                                      direct=annotation.direct,
                                                      ref=annotation.ref,
                                                      evidence=annotation.evidence,
                                                      date=annotation.date,
                                                      cross_annotated=annotation.cross_annotated))
        self.annotations = mapped_annotations_set

    def get_annotated_genes(self, include_cross_annotated=True):
        genes = []
        for annotation in self.annotations:
            if (not include_cross_annotated) and annotation.cross_annotated:
                continue
            genes.append(annotation.gid)
        return genes

    def remove_annotation(self, annot):
        try:
            self.annotations.remove(annot)
        except KeyError:
            return

    def add_annotation(self, gid, ref=None, cross_annotated=False,
                       allow_duplicate_gid=True, origin=None, ortho_evidence=None):
        if not allow_duplicate_gid:
            for annotated in self.annotations:
                if annotated.gid == gid:
                    return
        self.annotations.add(
            Annotation(
                gid=gid,
                ref=ref,
                cross_annotated=cross_annotated,
                origin=origin,
                ortho_evidence=ortho_evidence))

    def get_annotation_size(self):
        return len(self.annotations)

    def get_namespace(self):
        return self.namespace

    def get_xrefs(self, dbid):
        if dbid in self.xrefs:
            return self.xrefs[dbid]
        else:
            return None
